package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func newSystemTestApp(t *testing.T) *app {
	t.Helper()
	gin.SetMode(gin.TestMode)
	dsn := "file:" + strings.NewReplacer("/", "_", " ", "_").Replace(t.Name()) + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{TranslateError: true})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&DBUser{}, &DBProject{}, &DBAsset{}, &DBAssetDerivative{}, &DBUpload{}, &DBAlignment{}, &DBMeasurement{}, &DBSystemSetting{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	a := newApp(config{
		DataDir:          t.TempDir(),
		JWTSecret:        "system-test-secret",
		JWTExpiresIn:     time.Hour,
		MeshServiceURL:   "",
		RegisterCode:     "laochen",
		Environment:      "development",
		DBDriver:         "sqlite",
		WorkerCount:      2,
		UploadChunkLimit: 64 << 20,
		UploadFileLimit:  100 << 20,
	})
	a.db = db
	if err := a.reloadSettings(); err != nil {
		t.Fatalf("reload settings: %v", err)
	}
	return a
}

func mustCreateSystemUser(t *testing.T, a *app, username, password, role, status string) DBUser {
	t.Helper()
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.MinCost)
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	user := DBUser{Username: username, PasswordHash: string(hash), Role: role, Status: status, CreatedAt: time.Now(), UpdatedAt: time.Now()}
	if err := a.db.Create(&user).Error; err != nil {
		t.Fatalf("create user %s: %v", username, err)
	}
	return user
}

func systemContext(method, path, body string, user int64, role string) (*gin.Context, *httptest.ResponseRecorder) {
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest(method, path, bytes.NewBufferString(body))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("userID", user)
	if role != "" {
		c.Set("userRole", role)
	}
	return c, w
}

func decodeSystemResponse(t *testing.T, w *httptest.ResponseRecorder) (int, map[string]any) {
	t.Helper()
	var payload struct {
		Code int            `json:"code"`
		Msg  string         `json:"msg"`
		Data map[string]any `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode response %q: %v", w.Body.String(), err)
	}
	return payload.Code, payload.Data
}

func TestFirstRegisteredAccountBecomesAdmin(t *testing.T) {
	a := newSystemTestApp(t)

	first, w := systemContext(http.MethodPost, "/auth/register", `{"username":"owner","password":"owner123456","registerCode":"laochen"}`, 0, "")
	a.register(first)
	if w.Code != http.StatusCreated {
		t.Fatalf("first register status = %d, body = %s", w.Code, w.Body.String())
	}
	var firstUser DBUser
	if err := a.db.Where("username = ?", "owner").First(&firstUser).Error; err != nil {
		t.Fatalf("load first user: %v", err)
	}
	if normalizeUserRole(firstUser.Role) != roleAdmin {
		t.Fatalf("first account role = %q, want %q", firstUser.Role, roleAdmin)
	}

	second, w := systemContext(http.MethodPost, "/auth/register", `{"username":"member","password":"member123456","registerCode":"laochen"}`, 0, "")
	a.register(second)
	if w.Code != http.StatusCreated {
		t.Fatalf("second register status = %d, body = %s", w.Code, w.Body.String())
	}
	var secondUser DBUser
	if err := a.db.Where("username = ?", "member").First(&secondUser).Error; err != nil {
		t.Fatalf("load second user: %v", err)
	}
	if normalizeUserRole(secondUser.Role) != roleMember {
		t.Fatalf("second account role = %q, want %q", secondUser.Role, roleMember)
	}
}

func TestRegistrationSettingClosesSelfService(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)

	save, w := systemContext(http.MethodPatch, "/system/settings", `{"values":{"allow_registration":false}}`, admin.ID, roleAdmin)
	a.updateSettings(save)
	if w.Code != http.StatusOK {
		t.Fatalf("update settings status = %d, body = %s", w.Code, w.Body.String())
	}
	if a.allowRegistration() {
		t.Fatal("allowRegistration() = true after disabling self service")
	}

	register, w := systemContext(http.MethodPost, "/auth/register", `{"username":"blocked","password":"blocked123456","registerCode":"laochen"}`, 0, "")
	a.register(register)
	if w.Code != http.StatusForbidden {
		t.Fatalf("register status = %d, want 403, body = %s", w.Code, w.Body.String())
	}

	// The setting survives a cache reload, proving it is stored, not just cached.
	if err := a.reloadSettings(); err != nil {
		t.Fatalf("reload settings: %v", err)
	}
	if a.allowRegistration() {
		t.Fatal("allowRegistration() = true after reload")
	}
}

func TestSettingsRejectUnknownAndOutOfRangeValues(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)

	cases := []struct {
		name string
		body string
	}{
		{"unknown key", `{"values":{"unknown_key":"x"}}`},
		{"empty workspace name", `{"values":{"workspace_name":"   "}}`},
		{"workspace name too long", `{"values":{"workspace_name":"` + strings.Repeat("字", 65) + `"}}`},
		{"upload limit above deployment ceiling", `{"values":{"upload_file_limit":` + json.Number(formatInt64(a.cfg.UploadFileLimit+1)).String() + `}}`},
		{"upload limit below floor", `{"values":{"upload_file_limit":1024}}`},
		{"wrong boolean type", `{"values":{"allow_registration":"yes"}}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, w := systemContext(http.MethodPatch, "/system/settings", tc.body, admin.ID, roleAdmin)
			a.updateSettings(c)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400, body = %s", w.Code, w.Body.String())
			}
		})
	}

	// A rejected batch must not be partially applied.
	if err := a.reloadSettings(); err != nil {
		t.Fatalf("reload settings: %v", err)
	}
	if a.workspaceName() != "CloudBIM 工作区" {
		t.Fatalf("workspace name = %q, want the default value", a.workspaceName())
	}
	if a.uploadFileLimit() != a.cfg.UploadFileLimit {
		t.Fatalf("upload file limit = %d, want %d", a.uploadFileLimit(), a.cfg.UploadFileLimit)
	}
}

func formatInt64(value int64) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestSettingsPersistAndDriveUploadCeiling(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)

	body := `{"values":{"workspace_name":"  城东隧道  ","workspace_description":"结构复测工作区","allow_registration":false,"upload_file_limit":20971520}}`
	c, w := systemContext(http.MethodPatch, "/system/settings", body, admin.ID, roleAdmin)
	a.updateSettings(c)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
	}
	if got := a.workspaceName(); got != "城东隧道" {
		t.Fatalf("workspace name = %q, want trimmed value", got)
	}
	if got := a.uploadFileLimit(); got != 20971520 {
		t.Fatalf("upload file limit = %d, want 20971520", got)
	}

	// Out-of-range stored values fall back to the deployment ceiling instead of
	// widening what the server accepts.
	if err := a.db.Model(&DBSystemSetting{}).Where("key = ?", settingUploadFileLimit).Update("value", "999999999999").Error; err != nil {
		t.Fatalf("tamper setting: %v", err)
	}
	if err := a.reloadSettings(); err != nil {
		t.Fatalf("reload settings: %v", err)
	}
	if got := a.uploadFileLimit(); got != a.cfg.UploadFileLimit {
		t.Fatalf("upload file limit = %d, want fallback %d", got, a.cfg.UploadFileLimit)
	}
}

func TestSettingsRequireAdministrator(t *testing.T) {
	a := newSystemTestApp(t)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)

	router := gin.New()
	router.GET("/system/settings", func(c *gin.Context) { c.Set("userID", member.ID) }, a.adminRequired(), a.getSettings)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/system/settings", nil))
	if w.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403, body = %s", w.Code, w.Body.String())
	}
}

func TestMemberRoleGuards(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)

	selfChange, w := systemContext(http.MethodPatch, "/system/members/"+formatInt64(admin.ID), `{"role":"member"}`, admin.ID, roleAdmin)
	selfChange.Params = []gin.Param{{Key: "id", Value: formatInt64(admin.ID)}}
	a.updateMember(selfChange)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("self change status = %d, want 400, body = %s", w.Code, w.Body.String())
	}

	lastAdmin, w := systemContext(http.MethodPatch, "/system/members/1", `{"role":"member"}`, 999, roleAdmin)
	lastAdmin.Params = []gin.Param{{Key: "id", Value: formatInt64(admin.ID)}}
	a.updateMember(lastAdmin)
	if w.Code != http.StatusConflict {
		t.Fatalf("last admin demotion status = %d, want 409, body = %s", w.Code, w.Body.String())
	}

	invalidRole, w := systemContext(http.MethodPatch, "/system/members/1", `{"role":"owner"}`, admin.ID, roleAdmin)
	invalidRole.Params = []gin.Param{{Key: "id", Value: formatInt64(member.ID)}}
	a.updateMember(invalidRole)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("invalid role status = %d, want 400, body = %s", w.Code, w.Body.String())
	}

	promote, w := systemContext(http.MethodPatch, "/system/members/1", `{"role":"admin"}`, admin.ID, roleAdmin)
	promote.Params = []gin.Param{{Key: "id", Value: formatInt64(member.ID)}}
	a.updateMember(promote)
	if w.Code != http.StatusOK {
		t.Fatalf("promote status = %d, body = %s", w.Code, w.Body.String())
	}
	var promoted DBUser
	if err := a.db.First(&promoted, member.ID).Error; err != nil {
		t.Fatalf("load promoted member: %v", err)
	}
	if normalizeUserRole(promoted.Role) != roleAdmin {
		t.Fatalf("promoted role = %q, want %q", promoted.Role, roleAdmin)
	}

	// With two administrators the original one can step down.
	demote, w := systemContext(http.MethodPatch, "/system/members/1", `{"role":"member"}`, member.ID, roleAdmin)
	demote.Params = []gin.Param{{Key: "id", Value: formatInt64(admin.ID)}}
	a.updateMember(demote)
	if w.Code != http.StatusOK {
		t.Fatalf("demote status = %d, body = %s", w.Code, w.Body.String())
	}
}

func TestDisablingMemberEndsItsSessions(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)

	c, w := systemContext(http.MethodPatch, "/system/members/1", `{"status":"disabled"}`, admin.ID, roleAdmin)
	c.Params = []gin.Param{{Key: "id", Value: formatInt64(member.ID)}}
	a.updateMember(c)
	if w.Code != http.StatusOK {
		t.Fatalf("disable status = %d, body = %s", w.Code, w.Body.String())
	}
	var disabled DBUser
	if err := a.db.First(&disabled, member.ID).Error; err != nil {
		t.Fatalf("load member: %v", err)
	}
	if disabled.TokenVersion != member.TokenVersion+1 {
		t.Fatalf("token version = %d, want %d", disabled.TokenVersion, member.TokenVersion+1)
	}
	if normalizeUserStatus(disabled.Status) != userStatusDisabled {
		t.Fatalf("status = %q, want disabled", disabled.Status)
	}
}

func TestDeleteMemberGuardsOwnedWork(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)
	other := mustCreateSystemUser(t, a, "other", "other123456", roleMember, userStatusActive)

	project := DBProject{Name: "隧道复测", OwnerID: member.ID, CreatedAt: time.Now(), UpdatedAt: time.Now()}
	if err := a.db.Create(&project).Error; err != nil {
		t.Fatalf("create project: %v", err)
	}

	blocked, w := systemContext(http.MethodDelete, "/system/members/1", "", admin.ID, roleAdmin)
	blocked.Params = []gin.Param{{Key: "id", Value: formatInt64(member.ID)}}
	a.deleteMember(blocked)
	if w.Code != http.StatusConflict {
		t.Fatalf("delete owner status = %d, want 409, body = %s", w.Code, w.Body.String())
	}

	allowed, w := systemContext(http.MethodDelete, "/system/members/1", "", admin.ID, roleAdmin)
	allowed.Params = []gin.Param{{Key: "id", Value: formatInt64(other.ID)}}
	a.deleteMember(allowed)
	if w.Code != http.StatusOK {
		t.Fatalf("delete idle member status = %d, body = %s", w.Code, w.Body.String())
	}
	var remaining int64
	a.db.Model(&DBUser{}).Where("id = ?", other.ID).Count(&remaining)
	if remaining != 0 {
		t.Fatalf("remaining rows for deleted member = %d, want 0", remaining)
	}
}

func TestPasswordChangeInvalidatesOtherSessions(t *testing.T) {
	a := newSystemTestApp(t)
	account := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)

	// Sign a token for another device before the password changes.
	oldCtx, _ := systemContext(http.MethodGet, "/auth/me", "", account.ID, roleAdmin)
	oldToken, err := a.issueSession(oldCtx, account, time.Now())
	if err != nil {
		t.Fatalf("issue old session: %v", err)
	}

	router := gin.New()
	router.POST("/auth/password", a.authRequired(), a.changePassword)
	router.GET("/auth/me", a.authRequired(), a.me)

	change := httptest.NewRequest(http.MethodPost, "/auth/password", bytes.NewBufferString(`{"currentPassword":"admin123456","newPassword":"admin654321"}`))
	change.Header.Set("Content-Type", "application/json")
	change.Header.Set("Authorization", "Bearer "+oldToken)
	changeRecorder := httptest.NewRecorder()
	router.ServeHTTP(changeRecorder, change)
	if changeRecorder.Code != http.StatusOK {
		t.Fatalf("change password status = %d, body = %s", changeRecorder.Code, changeRecorder.Body.String())
	}
	var payload struct {
		Data struct {
			Token string `json:"token"`
		} `json:"data"`
	}
	if err := json.Unmarshal(changeRecorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode change password response: %v", err)
	}
	if payload.Data.Token == "" {
		t.Fatal("change password response did not return a replacement token")
	}

	stale := httptest.NewRequest(http.MethodGet, "/auth/me", nil)
	stale.Header.Set("Authorization", "Bearer "+oldToken)
	staleRecorder := httptest.NewRecorder()
	router.ServeHTTP(staleRecorder, stale)
	if staleRecorder.Code != http.StatusUnauthorized {
		t.Fatalf("stale token status = %d, want 401, body = %s", staleRecorder.Code, staleRecorder.Body.String())
	}

	fresh := httptest.NewRequest(http.MethodGet, "/auth/me", nil)
	fresh.Header.Set("Authorization", "Bearer "+payload.Data.Token)
	freshRecorder := httptest.NewRecorder()
	router.ServeHTTP(freshRecorder, fresh)
	if freshRecorder.Code != http.StatusOK {
		t.Fatalf("replacement token status = %d, body = %s", freshRecorder.Code, freshRecorder.Body.String())
	}
}

func TestDisabledAccountCannotUseExistingSession(t *testing.T) {
	a := newSystemTestApp(t)
	account := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)

	signCtx, _ := systemContext(http.MethodGet, "/auth/me", "", account.ID, roleAdmin)
	token, err := a.issueSession(signCtx, account, time.Now())
	if err != nil {
		t.Fatalf("issue session: %v", err)
	}
	account.Status = userStatusDisabled
	if err := a.db.Model(&DBUser{}).Where("id = ?", account.ID).Update("status", userStatusDisabled).Error; err != nil {
		t.Fatalf("disable account: %v", err)
	}

	router := gin.New()
	router.GET("/auth/me", a.authRequired(), a.me)
	request := httptest.NewRequest(http.MethodGet, "/auth/me", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("disabled account status = %d, want 403, body = %s", recorder.Code, recorder.Body.String())
	}
}

func TestListMembersHidesRosterFromNonAdmins(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)

	adminCtx, adminRecorder := systemContext(http.MethodGet, "/system/members", "", admin.ID, roleAdmin)
	a.listMembers(adminCtx)
	code, data := decodeSystemResponse(t, adminRecorder)
	if code != http.StatusOK {
		t.Fatalf("admin list code = %d", code)
	}
	if canManage, _ := data["canManage"].(bool); !canManage {
		t.Fatal("administrator cannot manage members")
	}
	if total, _ := data["total"].(float64); int(total) != 2 {
		t.Fatalf("admin total = %v, want 2", data["total"])
	}

	memberCtx, memberRecorder := systemContext(http.MethodGet, "/system/members", "", member.ID, roleMember)
	a.listMembers(memberCtx)
	code, data = decodeSystemResponse(t, memberRecorder)
	if code != http.StatusOK {
		t.Fatalf("member list code = %d", code)
	}
	if canManage, _ := data["canManage"].(bool); canManage {
		t.Fatal("member reports member management permission")
	}
	if total, _ := data["total"].(float64); int(total) != 1 {
		t.Fatalf("member total = %v, want only the caller", data["total"])
	}
}

func TestEnsureRoleDefaultsPromotesEarliestAccount(t *testing.T) {
	a := newSystemTestApp(t)
	// Accounts created before roles existed have no role and must be normalized.
	err := a.db.Exec("INSERT INTO db_users (id, username, password_hash, role, status, token_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
		int64(3), "legacy", "hash", "", "", 0, time.Now().Add(-time.Hour), time.Now()).Error
	if err != nil {
		t.Fatalf("insert legacy row: %v", err)
	}
	if err := a.ensureRoleDefaults(); err != nil {
		t.Fatalf("ensureRoleDefaults: %v", err)
	}
	var legacy DBUser
	if err := a.db.First(&legacy, 3).Error; err != nil {
		t.Fatalf("load legacy user: %v", err)
	}
	if normalizeUserRole(legacy.Role) != roleAdmin {
		t.Fatalf("legacy role = %q, want %q", legacy.Role, roleAdmin)
	}
	if normalizeUserStatus(legacy.Status) != userStatusActive {
		t.Fatalf("legacy status = %q, want %q", legacy.Status, userStatusActive)
	}

	// A second run must not create a second administrator.
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	if err := a.ensureRoleDefaults(); err != nil {
		t.Fatalf("second ensureRoleDefaults: %v", err)
	}
	if got := a.countActiveAdmins(); got != 2 {
		t.Fatalf("active admin count = %d, want 2", got)
	}
	if normalizeUserRole(admin.Role) != roleAdmin {
		t.Fatalf("existing admin role = %q", admin.Role)
	}
}

func TestSystemInfoReportsWorkspaceScopeAndRole(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "admin123456", roleAdmin, userStatusActive)
	member := mustCreateSystemUser(t, a, "member", "member123456", roleMember, userStatusActive)
	project := DBProject{Name: "隧道复测", OwnerID: member.ID, CreatedAt: time.Now(), UpdatedAt: time.Now()}
	if err := a.db.Create(&project).Error; err != nil {
		t.Fatalf("create project: %v", err)
	}
	asset := DBAsset{Type: "pointcloud", SourceName: "scan.las", SourceSize: 2048, Status: "ready", CreatedAt: time.Now().Unix(), OwnerID: member.ID, ProjectID: project.ID, Dir: t.TempDir()}
	if err := a.db.Create(&asset).Error; err != nil {
		t.Fatalf("create asset: %v", err)
	}

	memberCtx, memberRecorder := systemContext(http.MethodGet, "/system/info", "", member.ID, roleMember)
	memberCtx.Set("sessionExpiresAt", time.Now().Add(time.Hour))
	a.systemInfo(memberCtx)
	code, data := decodeSystemResponse(t, memberRecorder)
	if code != http.StatusOK {
		t.Fatalf("system info code = %d, body = %s", code, memberRecorder.Body.String())
	}
	if data["role"] != roleMember {
		t.Fatalf("role = %v, want %q", data["role"], roleMember)
	}
	if count, _ := data["projectCount"].(float64); int(count) != 1 {
		t.Fatalf("member projectCount = %v, want 1", data["projectCount"])
	}
	workspace, ok := data["workspace"].(map[string]any)
	if !ok {
		t.Fatalf("workspace block missing: %#v", data["workspace"])
	}
	if count, _ := workspace["memberCount"].(float64); int(count) != 2 {
		t.Fatalf("workspace memberCount = %v, want 2", workspace["memberCount"])
	}
	storage, ok := data["storage"].(map[string]any)
	if !ok {
		t.Fatalf("storage block missing: %#v", data["storage"])
	}
	if bytes, _ := storage["sourceBytes"].(float64); int64(bytes) != 2048 {
		t.Fatalf("storage sourceBytes = %v, want 2048", storage["sourceBytes"])
	}
	if _, exists := data["dataDir"]; exists {
		t.Fatal("non-admin response exposed the server data directory")
	}
	if _, exists := data["sessionExpiresAt"]; !exists {
		t.Fatal("session expiry missing from system info")
	}

	adminCtx, adminRecorder := systemContext(http.MethodGet, "/system/info", "", admin.ID, roleAdmin)
	a.systemInfo(adminCtx)
	if _, data := decodeSystemResponse(t, adminRecorder); data["dataDir"] == nil {
		t.Fatal("administrator response is missing the data directory")
	}
}
