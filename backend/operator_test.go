package main

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func operatorTestRouter(a *app) *gin.Engine {
	r := gin.New()
	r.Use(a.authRequired())
	r.GET("/auth/me", a.me)
	r.GET("/operator/tasks", a.operatorTasks)
	r.GET("/operator/tasks/:scanId/result", a.operatorResult)
	r.GET("/operator/tasks/:scanId/geometry", a.operatorGeometry)
	r.GET("/operator/reports/:version/actions", a.operatorListActions)
	r.POST("/operator/reports/:version/actions", a.operatorCreateAction)
	r.POST("/system/members", a.adminRequired(), a.createOperatorMember)
	r.PATCH("/system/members/:id", a.adminRequired(), a.updateMember)
	for _, route := range []struct{ method, path string }{{"GET", "/system/members"}, {"GET", "/projects"}, {"POST", "/uploads"}, {"DELETE", "/assets/1"}, {"POST", "/alignments/bim/c2m"}, {"GET", "/assets/1/pointcloud.las"}} {
		r.Handle(route.method, route.path, func(c *gin.Context) { c.String(200, "legacy handler reached") })
	}
	return r
}

func operatorTestToken(t *testing.T, a *app, account DBUser) string {
	t.Helper()
	c, _ := systemContext("POST", "/auth/login", "", account.ID, account.Role)
	token, err := a.issueSession(c, account, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func operatorHTTPRequest(r *gin.Engine, token, method, path, body string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(rec, req)
	return rec
}

func TestOperatorAssignedTaskBoundaryAndSharedActions(t *testing.T) {
	a := newSystemTestApp(t)
	if err := a.db.AutoMigrate(&DBC2MResult{}, &DBC2MReportRun{}, &DBC2MReportBar{}, &DBInspectionAction{}); err != nil {
		t.Fatal(err)
	}
	ownerA := mustCreateSystemUser(t, a, "owner-a", "test-password", roleAdmin, userStatusActive)
	ownerB := mustCreateSystemUser(t, a, "owner-b", "test-password", roleMember, userStatusActive)
	projectA := DBProject{Name: "Assigned", OwnerID: ownerA.ID}
	projectB := DBProject{Name: "Other", OwnerID: ownerB.ID}
	for _, p := range []*DBProject{&projectA, &projectB} {
		if err := a.db.Create(p).Error; err != nil {
			t.Fatal(err)
		}
	}
	operator := mustCreateSystemUser(t, a, "operator", "test-password", roleOperator, userStatusActive)
	operator.OperatorProjectID = &projectA.ID
	if err := a.db.Model(&operator).Update("operator_project_id", projectA.ID).Error; err != nil {
		t.Fatal(err)
	}
	scans := []DBAsset{{ID: 101, OwnerID: ownerA.ID, ProjectID: projectA.ID, Type: "pointcloud", Status: "ready", SourceName: "assigned.las", LinkedBimID: int64Ptr(102)}, {ID: 102, OwnerID: ownerA.ID, ProjectID: projectA.ID, Type: "bim", Status: "ready", SourceName: "design.ifc"}, {ID: 201, OwnerID: ownerB.ID, ProjectID: projectB.ID, Type: "pointcloud", Status: "ready", LinkedBimID: int64Ptr(202)}, {ID: 202, OwnerID: ownerB.ID, ProjectID: projectB.ID, Type: "bim", Status: "ready"}, {ID: 103, OwnerID: ownerA.ID, ProjectID: projectA.ID, Type: "bim", Status: "ready"}}
	for i := range scans {
		if err := a.db.Create(&scans[i]).Error; err != nil {
			t.Fatal(err)
		}
	}
	fingerprint := installOperatorFreshInputs(t, a, 101, 102)
	rows := []DBC2MResult{{OwnerID: ownerA.ID, ScanID: 101, BimID: 102, InputFingerprint: fingerprint, DiagnosticsJSON: `{"rebarComparison":{"bars":[{"ifcGlobalId":"bar-a","status":"missing"}]}}`}, {OwnerID: ownerB.ID, ScanID: 201, BimID: 202, InputFingerprint: "foreign-input", DiagnosticsJSON: `{"rebarComparison":{"bars":[{"ifcGlobalId":"bar-b","status":"missing"}]}}`}}
	for i := range rows {
		if err := a.db.Transaction(func(tx *gorm.DB) error {
			if err := tx.Create(&rows[i]).Error; err != nil {
				return err
			}
			return a.createC2MReportSnapshot(tx, &rows[i])
		}); err != nil {
			t.Fatal(err)
		}
	}
	version, otherVersion := c2mResultVersion(rows[0]), c2mResultVersion(rows[1])
	// The DB role must win even if a signed token still claims member.
	staleClaims := operator
	staleClaims.Role = roleMember
	token := operatorTestToken(t, a, staleClaims)
	ownerToken := operatorTestToken(t, a, ownerA)
	r := operatorTestRouter(a)
	rec := operatorHTTPRequest(r, token, "GET", "/operator/tasks", "")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"scanId":101`) || strings.Contains(rec.Body.String(), `"scanId":201`) || !strings.Contains(rec.Body.String(), `"connected":false`) {
		t.Fatal(rec.Code, rec.Body.String())
	}
	for _, tok := range []string{token, ownerToken} {
		rec = operatorHTTPRequest(r, tok, "GET", "/operator/tasks/101/result?bimId=102", "")
		if rec.Code != 200 || !strings.Contains(rec.Body.String(), version) || !strings.Contains(rec.Body.String(), `"fresh":true`) {
			t.Fatal(rec.Code, rec.Body.String())
		}
	}
	for _, path := range []string{"/operator/tasks/201/result?bimId=202", "/operator/tasks/101/result?bimId=202", "/operator/tasks/101/result?bimId=103", "/operator/tasks/201/geometry?bimId=202&version=" + otherVersion, "/operator/reports/" + otherVersion + "/actions", "/operator/reports/unknown/actions"} {
		rec = operatorHTTPRequest(r, token, "GET", path, "")
		if rec.Code != 404 {
			t.Fatalf("%s: %d %s", path, rec.Code, rec.Body.String())
		}
	}
	for _, route := range []struct{ method, path string }{{"GET", "/system/members"}, {"GET", "/projects"}, {"POST", "/uploads"}, {"DELETE", "/assets/1"}, {"POST", "/alignments/bim/c2m"}, {"GET", "/assets/1/pointcloud.las"}} {
		if rec = operatorHTTPRequest(r, token, route.method, route.path, `{}`); rec.Code != 403 {
			t.Fatal(route, rec.Code, rec.Body.String())
		}
	}
	actionURL := "/operator/reports/" + version + "/actions"
	body := func(step string, demo bool) string {
		return inspectionActionBody("bar-a", step, "安排调整并复检", demo)
	}
	for _, step := range []string{"record_adjustment", "request_recheck"} {
		if rec = operatorHTTPRequest(r, token, "POST", actionURL, body(step, false)); rec.Code != 409 {
			t.Fatal(rec.Code, rec.Body.String())
		}
	}
	if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("acknowledge", false)); rec.Code != 201 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	// Owner and operator share one chain, so changing actors cannot duplicate a step.
	if rec = operatorHTTPRequest(r, ownerToken, "POST", actionURL, body("acknowledge", false)); rec.Code != 409 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, ownerToken, "POST", actionURL, body("record_adjustment", false)); rec.Code != 201 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("request_recheck", false)); rec.Code != 201 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("request_recheck", false)); rec.Code != 409 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("acknowledge", true)); rec.Code != 201 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	var events []DBInspectionAction
	if err := a.db.Order("id ASC").Find(&events).Error; err != nil {
		t.Fatal(err)
	}
	if len(events) != 4 || events[0].ActorID != operator.ID || events[1].ActorID != ownerA.ID || events[2].ActorID != operator.ID || events[0].OwnerID != ownerA.ID || !events[3].Demonstration {
		t.Fatalf("wrong actor or chain: %+v", events)
	}
	var saved DBC2MReportRun
	if err := a.db.Where("result_version = ?", version).First(&saved).Error; err != nil || saved.DiagnosticsJSON != rows[0].DiagnosticsJSON {
		t.Fatal("report modified", err, saved)
	}
	if rec = operatorHTTPRequest(r, token, "GET", actionURL, ""); rec.Code != 200 || !strings.Contains(rec.Body.String(), `"actorId":`) {
		t.Fatal(rec.Code, rec.Body.String())
	}

	// Input edits invalidate both real and demonstration writes, while old events
	// remain readable. Denials must leave the append-only action table unchanged.
	originalMatrix := `[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]`
	if err := a.db.Model(&DBAlignment{}).Where("scan_id = ?", 101).Update("matrix_json", `[1,0,0,0,0,1,0,0,0,0,1,0,1,0,0,1]`).Error; err != nil {
		t.Fatal(err)
	}
	assertNoWrite := func() {
		t.Helper()
		var count int64
		if err := a.db.Model(&DBInspectionAction{}).Count(&count).Error; err != nil || count != 4 {
			t.Fatal("denied action wrote records", count, err)
		}
	}
	for _, demonstration := range []bool{false, true} {
		if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("record_adjustment", demonstration)); rec.Code != 409 {
			t.Fatal("stale action accepted", rec.Code, rec.Body.String())
		}
		assertNoWrite()
	}
	if rec = operatorHTTPRequest(r, token, "GET", actionURL, ""); rec.Code != 200 {
		t.Fatal("historical read denied", rec.Code)
	}
	if err := a.db.Model(&DBAlignment{}).Where("scan_id = ?", 101).Update("matrix_json", originalMatrix).Error; err != nil {
		t.Fatal(err)
	}
	// Same input, different result artifacts: only the current report may accept
	// actions, even when its historical input still passes freshness checks.
	if err := a.db.Model(&DBC2MResult{}).Where("id = ?", rows[0].ID).Update("colored_ply_path", "replaced-artifact.ply").Error; err != nil {
		t.Fatal(err)
	}
	for _, demonstration := range []bool{false, true} {
		if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("record_adjustment", demonstration)); rec.Code != 409 {
			t.Fatal("superseded action accepted", rec.Code, rec.Body.String())
		}
		assertNoWrite()
	}
	if rec = operatorHTTPRequest(r, token, "GET", actionURL, ""); rec.Code != 200 {
		t.Fatal("historical read denied", rec.Code)
	}
	if err := a.db.Model(&operator).Update("operator_project_id", projectB.ID).Error; err != nil {
		t.Fatal(err)
	}
	if rec = operatorHTTPRequest(r, token, "GET", "/operator/tasks/101/result?bimId=102", ""); rec.Code != 404 {
		t.Fatal("reassignment not enforced", rec.Code)
	}
	if err := a.db.Model(&operator).Update("operator_project_id", nil).Error; err != nil {
		t.Fatal(err)
	}
	if rec = operatorHTTPRequest(r, token, "GET", "/operator/tasks", ""); rec.Code != 200 || !strings.Contains(rec.Body.String(), `"items":[]`) {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "POST", actionURL, body("acknowledge", true)); rec.Code != 404 {
		t.Fatal("revoked assignment wrote action", rec.Code)
	}
	if err := a.db.Model(&operator).Update("status", userStatusDisabled).Error; err != nil {
		t.Fatal(err)
	}
	if rec = operatorHTTPRequest(r, token, "GET", "/operator/tasks", ""); rec.Code != 403 {
		t.Fatal("disabled account accepted", rec.Code)
	}
}

func int64Ptr(value int64) *int64 { return &value }

func installOperatorFreshInputs(t *testing.T, a *app, scanID, bimID int64) string {
	t.Helper()
	// The in-process fixture service shares this host's temporary data directory.
	a.cfg.MeshServiceStorageDir = a.cfg.DataDir
	var scan DBAsset
	if err := a.db.First(&scan, scanID).Error; err != nil {
		t.Fatal(err)
	}
	for _, id := range []int64{scanID, bimID} {
		var asset DBAsset
		if err := a.db.First(&asset, id).Error; err != nil {
			t.Fatal(err)
		}
		if asset.Dir == "" {
			asset.Dir = filepath.Join(a.cfg.DataDir, fmt.Sprintf("operator-fixture-%d", id))
			if err := a.db.Model(&asset).Update("dir", asset.Dir).Error; err != nil {
				t.Fatal(err)
			}
		}
		if err := os.MkdirAll(asset.Dir, 0755); err != nil {
			t.Fatal(err)
		}
		files := map[string]string{"source.las": "raw scan"}
		if id == bimID {
			files = map[string]string{"source": "IFC", "metadata.json": "{}"}
		}
		for name, content := range files {
			path := filepath.Join(asset.Dir, name)
			if !regularFileExists(path) {
				if err := os.WriteFile(path, []byte(content), 0644); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	if err := a.db.First(&scan, scanID).Error; err != nil {
		t.Fatal(err)
	}
	var uploadCount int64
	if err := a.db.Model(&DBUpload{}).Where("asset_id = ?", bimID).Count(&uploadCount).Error; err != nil {
		t.Fatal(err)
	}
	if uploadCount == 0 {
		var bim DBAsset
		if err := a.db.First(&bim, bimID).Error; err != nil {
			t.Fatal(err)
		}
		if err := a.db.Create(&DBUpload{ID: fmt.Sprintf("operator-bim-%d", bimID), AssetID: bimID, OwnerID: scan.OwnerID, Status: "ready", Dir: bim.Dir}).Error; err != nil {
			t.Fatal(err)
		}
	}
	alignment := DBAlignment{ScanID: scanID, BimID: bimID, OwnerID: scan.OwnerID, MatrixJSON: `[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]`}
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, scan.OwnerID).FirstOrCreate(&alignment).Error; err != nil {
		t.Fatal(err)
	}
	var preprocessCount int64
	if err := a.db.Model(&DBAssetDerivative{}).Where("asset_id = ? AND kind = ?", scanID, tableFreeKind).Count(&preprocessCount).Error; err != nil {
		t.Fatal(err)
	}
	if preprocessCount == 0 {
		installPreprocessedScan(t, a, assetFromDB(scan))
	}
	var bim DBAsset
	if err := a.db.First(&bim, bimID).Error; err != nil {
		t.Fatal(err)
	}
	verified := verifiedLegacyRemeshAsset(t, bim.Dir)
	if err := a.db.Model(&bim).Updates(map[string]any{"remesh_status": "succeeded", "remesh_algorithm": verified.RemeshAlgorithm, "remesh_params_json": verified.RemeshParamsJSON, "remesh_input_hash": verified.RemeshInputHash, "remesh_implementation_version": verified.RemeshImplementationVersion, "remesh_contract_version": verified.RemeshContractVersion, "remesh_fingerprint": verified.RemeshFingerprint, "remesh_content_hash": verified.RemeshContentHash}).Error; err != nil {
		t.Fatal(err)
	}
	paths, err := AnalysisMeshStoragePaths(assetFromDB(bim), "am-operator-test")
	if err != nil {
		t.Fatal(err)
	}
	manifest := writeAnalysisMeshArtifact(t, paths.FinalPath)
	size, err := ValidateAnalysisMeshManifest(paths.FinalPath, manifest)
	if err != nil {
		t.Fatal(err)
	}
	derivative, err := AnalysisMeshDerivativeRow(bimID, paths, manifest, size, "test")
	if err != nil {
		t.Fatal(err)
	}
	if err := a.db.Create(&derivative).Error; err != nil {
		t.Fatal(err)
	}
	installDenoiseService(t, a, nil)
	c, w := rebarContext("POST", "/", fmt.Sprintf(`{"modelScanFileId":%d,"modelBimFileId":%d}`, scanID, bimID), scan.OwnerID)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	fingerprint, err := a.currentC2MInputFingerprint(scan.ID, bimID, scan.OwnerID)
	if err != nil {
		t.Fatal(err)
	}
	return fingerprint
}

func TestOperatorGeometryExactVersionAndFreshInputs(t *testing.T) {
	a, scan := denoiseTestApp(t)
	a.cfg.JWTSecret, a.cfg.JWTExpiresIn = "operator-test", time.Hour
	if err := a.db.AutoMigrate(&DBUser{}, &DBProject{}, &DBC2MResult{}, &DBC2MReportRun{}, &DBC2MReportBar{}, &DBInspectionAction{}); err != nil {
		t.Fatal(err)
	}
	project := DBProject{ID: 50, OwnerID: 10, Name: "Assigned"}
	operator := DBUser{ID: 30, Username: "operator", PasswordHash: "unused", Role: roleOperator, Status: userStatusActive, OperatorProjectID: &project.ID}
	for _, item := range []any{&project, &operator} {
		if err := a.db.Create(item).Error; err != nil {
			t.Fatal(err)
		}
	}
	if err := a.db.Model(&DBAsset{}).Where("id IN ?", []int64{1, 2}).Update("project_id", project.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := a.db.Model(&DBAsset{}).Where("id = 1").Update("linked_bim_id", 2).Error; err != nil {
		t.Fatal(err)
	}
	fingerprint := installOperatorFreshInputs(t, a, scan.ID, 2)
	ply := []byte("ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n")
	colored := filepath.Join(a.cfg.DataDir, "c2m_results", "operator.ply")
	if err := os.MkdirAll(filepath.Dir(colored), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(colored, ply, 0644); err != nil {
		t.Fatal(err)
	}
	row := DBC2MResult{OwnerID: 10, ScanID: 1, BimID: 2, InputFingerprint: fingerprint, ColoredPlyPath: colored, DiagnosticsJSON: `{}`}
	if err := a.db.Create(&row).Error; err != nil {
		t.Fatal(err)
	}
	r, token := operatorTestRouter(a), operatorTestToken(t, a, operator)
	url := "/operator/tasks/1/geometry?bimId=2"
	for _, test := range []struct {
		suffix string
		status int
	}{{"", 400}, {"&version=wrong", 409}, {"&version=" + c2mResultVersion(row), 200}} {
		rec := operatorHTTPRequest(r, token, "GET", url+test.suffix, "")
		if rec.Code != test.status {
			t.Fatal(test, rec.Code, rec.Body.String())
		}
		if test.status == 200 && (rec.Body.String() != string(ply) || rec.Header().Get("X-C2M-Result-Version") != c2mResultVersion(row)) {
			t.Fatal("wrong geometry or version")
		}
	}
	if err := a.db.Model(&DBAlignment{}).Where("scan_id = 1").Update("matrix_json", `[1,0,0,0,0,1,0,0,0,0,1,0,1,0,0,1]`).Error; err != nil {
		t.Fatal(err)
	}
	if rec := operatorHTTPRequest(r, token, "GET", url+"&version="+c2mResultVersion(row), ""); rec.Code != 409 {
		t.Fatal("stale geometry served", rec.Code)
	}
}

func TestOperatorMemberCreateAssignmentAndRoleGuards(t *testing.T) {
	a := newSystemTestApp(t)
	admin := mustCreateSystemUser(t, a, "admin", "password123", roleAdmin, userStatusActive)
	other := mustCreateSystemUser(t, a, "other", "password123", roleMember, userStatusActive)
	projects := []DBProject{{OwnerID: admin.ID, Name: "Own"}, {OwnerID: other.ID, Name: "Other"}}
	for i := range projects {
		if err := a.db.Create(&projects[i]).Error; err != nil {
			t.Fatal(err)
		}
	}
	r, token := operatorTestRouter(a), operatorTestToken(t, a, admin)
	for _, test := range []struct {
		tok, body string
		status    int
	}{{operatorTestToken(t, a, other), `{"username":"blocked","password":"password123"}`, 403}, {token, fmt.Sprintf(`{"username":"wrong","password":"password123","operatorProjectId":%d}`, projects[1].ID), 400}, {token, `{"username":"injected","password":"password123","role":"admin"}`, 400}} {
		if rec := operatorHTTPRequest(r, test.tok, "POST", "/system/members", test.body); rec.Code != test.status {
			t.Fatal(test.status, rec.Code, rec.Body.String())
		}
	}
	body := fmt.Sprintf(`{"username":"worker","password":"password123","displayName":"操作员","operatorProjectId":%d}`, projects[0].ID)
	rec := operatorHTTPRequest(r, token, "POST", "/system/members", body)
	if rec.Code != 201 || strings.Contains(rec.Body.String(), "PasswordHash") || strings.Contains(rec.Body.String(), "$2") {
		t.Fatal(rec.Code, rec.Body.String())
	}
	var worker DBUser
	if err := a.db.Where("username = ?", "worker").First(&worker).Error; err != nil || worker.Role != roleOperator || worker.OperatorProjectID == nil || *worker.OperatorProjectID != projects[0].ID {
		t.Fatal(err, worker)
	}
	if rec = operatorHTTPRequest(r, token, "POST", "/system/members", body); rec.Code != 409 {
		t.Fatal("duplicate username accepted", rec.Code)
	}
	c, w := systemContext("POST", "/auth/login", `{"username":"worker","password":"password123"}`, 0, "")
	a.login(c)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"role":"operator"`) || !strings.Contains(w.Body.String(), `"operatorProjectId":`) {
		t.Fatal(w.Code, w.Body.String())
	}
	workerToken := operatorTestToken(t, a, worker)
	if rec = operatorHTTPRequest(r, workerToken, "GET", "/auth/me", ""); rec.Code != 200 || !strings.Contains(rec.Body.String(), `"operatorProjectId":`) {
		t.Fatal(rec.Code, rec.Body.String())
	}
	patchURL := fmt.Sprintf("/system/members/%d", worker.ID)
	for _, body := range []string{`{"role":"invalid"}`, fmt.Sprintf(`{"operatorProjectId":%d}`, projects[1].ID), `{"operatorProjectId":0}`, `{"operatorProjectId":"1"}`} {
		if rec = operatorHTTPRequest(r, token, "PATCH", patchURL, body); rec.Code != 400 {
			t.Fatal(body, rec.Code, rec.Body.String())
		}
	}
	if rec = operatorHTTPRequest(r, token, "PATCH", patchURL, `{"operatorProjectId":null}`); rec.Code != 200 || !strings.Contains(rec.Body.String(), `"operatorProjectId":null`) {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "PATCH", fmt.Sprintf("/system/members/%d", other.ID), fmt.Sprintf(`{"operatorProjectId":%d}`, projects[0].ID)); rec.Code != 400 {
		t.Fatal("member accepted operator assignment", rec.Code)
	}
	if rec = operatorHTTPRequest(r, token, "PATCH", patchURL, fmt.Sprintf(`{"role":"operator","operatorProjectId":%d}`, projects[0].ID)); rec.Code != 200 {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "PATCH", patchURL, `{"role":"member"}`); rec.Code != 200 || !strings.Contains(rec.Body.String(), `"operatorProjectId":null`) {
		t.Fatal(rec.Code, rec.Body.String())
	}
	if rec = operatorHTTPRequest(r, token, "PATCH", fmt.Sprintf("/system/members/%d", admin.ID), `{"role":"operator"}`); rec.Code != 400 {
		t.Fatal("self-demotion allowed", rec.Code)
	}
	c, w = systemContext("POST", "/auth/register", `{"username":"public","password":"password123","registerCode":"laochen","role":"operator","operatorProjectId":1}`, 0, "")
	a.register(c)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	var public DBUser
	if err := a.db.Where("username = ?", "public").First(&public).Error; err != nil || public.Role != roleMember || public.OperatorProjectID != nil {
		t.Fatal("registration role injected", err, public)
	}
	var data map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &data); err != nil {
		t.Fatal(err)
	}
}

func TestOperatorSecondAdminCanDisableWithoutChangingAssignment(t *testing.T) {
	a := newSystemTestApp(t)
	adminA := mustCreateSystemUser(t, a, "admin-a", "password123", roleAdmin, userStatusActive)
	adminB := mustCreateSystemUser(t, a, "admin-b", "password123", roleAdmin, userStatusActive)
	worker := mustCreateSystemUser(t, a, "worker", "password123", roleOperator, userStatusActive)
	projects := []DBProject{{OwnerID: adminA.ID, Name: "Assigned A"}, {OwnerID: adminA.ID, Name: "Another A"}, {OwnerID: adminB.ID, Name: "Own B"}}
	for i := range projects {
		if err := a.db.Create(&projects[i]).Error; err != nil {
			t.Fatal(err)
		}
	}
	worker.OperatorProjectID = &projects[0].ID
	if err := a.db.Model(&worker).Update("operator_project_id", projects[0].ID).Error; err != nil {
		t.Fatal(err)
	}
	r, tokenB := operatorTestRouter(a), operatorTestToken(t, a, adminB)
	originalWorkerToken := operatorTestToken(t, a, worker)
	url := fmt.Sprintf("/system/members/%d", worker.ID)
	// Omitted project and the UI's explicitly unchanged project are equivalent.
	for i, body := range []string{`{"status":"disabled"}`, fmt.Sprintf(`{"role":"operator","status":"disabled","operatorProjectId":%d}`, projects[0].ID)} {
		rec := operatorHTTPRequest(r, tokenB, "PATCH", url, body)
		if rec.Code != 200 {
			t.Fatal("admin B could not disable", rec.Code, rec.Body.String())
		}
		var saved DBUser
		if err := a.db.First(&saved, worker.ID).Error; err != nil || saved.Status != userStatusDisabled || saved.OperatorProjectID == nil || *saved.OperatorProjectID != projects[0].ID || saved.TokenVersion != i+1 {
			t.Fatal("disable changed assignment or did not revoke", err, saved)
		}
		if rec = operatorHTTPRequest(r, originalWorkerToken, "GET", "/operator/tasks", ""); rec.Code != 403 {
			t.Fatal("disabled session accepted", rec.Code)
		}
		if rec = operatorHTTPRequest(r, tokenB, "PATCH", url, fmt.Sprintf(`{"status":"active","operatorProjectId":%d}`, projects[0].ID)); rec.Code != 200 {
			t.Fatal(rec.Code, rec.Body.String())
		}
		if rec = operatorHTTPRequest(r, originalWorkerToken, "GET", "/operator/tasks", ""); rec.Code != 401 {
			t.Fatal("reactivation restored revoked session", rec.Code)
		}
	}
	rec := operatorHTTPRequest(r, tokenB, "PATCH", url, fmt.Sprintf(`{"operatorProjectId":%d}`, projects[1].ID))
	if rec.Code != 400 {
		t.Fatal("admin B assigned another admin's new project", rec.Code, rec.Body.String())
	}
	var saved DBUser
	if err := a.db.First(&saved, worker.ID).Error; err != nil || saved.OperatorProjectID == nil || *saved.OperatorProjectID != projects[0].ID {
		t.Fatal("rejected reassignment changed worker", err, saved)
	}
	// Entering operator is a fresh authorization decision even if a legacy row
	// already has the same project ID.
	if err := a.db.Model(&worker).Update("role", roleMember).Error; err != nil {
		t.Fatal(err)
	}
	rec = operatorHTTPRequest(r, tokenB, "PATCH", url, fmt.Sprintf(`{"role":"operator","operatorProjectId":%d}`, projects[0].ID))
	if rec.Code != 400 {
		t.Fatal("operator role entry bypassed assignment ownership", rec.Code, rec.Body.String())
	}
}
