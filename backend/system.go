package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// System management keeps three separate concerns: the signed-in account
// (profile, password, sessions), the workspace itself (members, roles, shared
// settings) and the runtime status of the service. Everything below is scoped to
// a single workspace, which this deployment models as one database.
const (
	sessionCookieName = "cloudbim_session"

	roleAdmin  = "admin"
	roleMember = "member"

	userStatusActive   = "active"
	userStatusDisabled = "disabled"

	settingWorkspaceName        = "workspace_name"
	settingWorkspaceDescription = "workspace_description"
	settingAllowRegistration    = "allow_registration"
	settingUploadFileLimit      = "upload_file_limit"

	settingKindString  = "string"
	settingKindText    = "text"
	settingKindBoolean = "boolean"
	settingKindBytes   = "bytes"

	minUploadFileLimit = 1 << 20
)

func normalizeUserRole(role string) string {
	if strings.EqualFold(strings.TrimSpace(role), roleAdmin) {
		return roleAdmin
	}
	return roleMember
}

func normalizeUserStatus(status string) string {
	if strings.EqualFold(strings.TrimSpace(status), userStatusDisabled) {
		return userStatusDisabled
	}
	return userStatusActive
}

func roleLabel(role string) string {
	if normalizeUserRole(role) == roleAdmin {
		return "管理员"
	}
	return "项目成员"
}

// tokenVersionClaim reads the session generation. Tokens minted before session
// invalidation existed carry no claim and therefore match the default version.
func tokenVersionClaim(claims map[string]any) int {
	raw, ok := claims["ver"]
	if !ok {
		return 0
	}
	value, err := strconv.Atoi(strings.TrimSpace(toString(raw)))
	if err != nil || value < 0 {
		return 0
	}
	return value
}

func toString(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case float64:
		return strconv.FormatInt(int64(typed), 10)
	case json.Number:
		return typed.String()
	default:
		return ""
	}
}

func (a *app) loadAccount(id int64) (DBUser, error) {
	if id == 0 {
		return DBUser{}, errors.New("账号不存在")
	}
	var account DBUser
	if err := a.db.First(&account, id).Error; err != nil {
		return DBUser{}, err
	}
	return account, nil
}

// ensureRoleDefaults normalizes accounts created before roles existed and makes
// sure the workspace always has at least one administrator.
func (a *app) ensureRoleDefaults() error {
	if err := a.db.Model(&DBUser{}).Where("role = '' OR role IS NULL").Update("role", roleMember).Error; err != nil {
		return err
	}
	if err := a.db.Model(&DBUser{}).Where("status = '' OR status IS NULL").Update("status", userStatusActive).Error; err != nil {
		return err
	}
	var adminCount int64
	if err := a.db.Model(&DBUser{}).Where("role = ?", roleAdmin).Count(&adminCount).Error; err != nil {
		return err
	}
	if adminCount > 0 {
		return nil
	}
	var first DBUser
	if err := a.db.Order("created_at asc, id asc").First(&first).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		return err
	}
	return a.db.Model(&DBUser{}).Where("id = ?", first.ID).Update("role", roleAdmin).Error
}

func (a *app) countActiveAdmins() int64 {
	var count int64
	if err := a.db.Model(&DBUser{}).Where("role = ? AND status = ?", roleAdmin, userStatusActive).Count(&count).Error; err != nil {
		return 0
	}
	return count
}

// ---------------------------------------------------------------------------
// Workspace settings
// ---------------------------------------------------------------------------

// settingDescriptor is the allowlist contract: only these keys can be written,
// each with its own type and bounds. Environment values remain the hard ceiling.
type settingDescriptor struct {
	Key         string
	Label       string
	Description string
	Kind        string
	MaxLength   int
	MinBytes    int64
	MaxBytes    int64
}

func (a *app) settingDescriptors() []settingDescriptor {
	return []settingDescriptor{
		{
			Key:         settingWorkspaceName,
			Label:       "工作区名称",
			Description: "显示在系统管理与服务信息中，最长 64 个字符。",
			Kind:        settingKindString,
			MaxLength:   64,
		},
		{
			Key:         settingWorkspaceDescription,
			Label:       "工作区说明",
			Description: "用于说明该工作区的用途与适用范围，最长 200 个字符。",
			Kind:        settingKindText,
			MaxLength:   200,
		},
		{
			Key:         settingAllowRegistration,
			Label:       "允许自助注册",
			Description: "关闭后注册接口直接拒绝新账号，仅管理员可以开通成员。",
			Kind:        settingKindBoolean,
		},
		{
			Key:         settingUploadFileLimit,
			Label:       "单文件上传上限",
			Description: "在部署环境上限之内调整单个文件的大小限制，单位为字节。",
			Kind:        settingKindBytes,
			MinBytes:    minUploadFileLimit,
			MaxBytes:    a.cfg.UploadFileLimit,
		},
	}
}

func (a *app) settingDefault(descriptor settingDescriptor) string {
	switch descriptor.Key {
	case settingWorkspaceName:
		return "CloudBIM 工作区"
	case settingWorkspaceDescription:
		return ""
	case settingAllowRegistration:
		return "true"
	case settingUploadFileLimit:
		return strconv.FormatInt(a.cfg.UploadFileLimit, 10)
	default:
		return ""
	}
}

func (a *app) settingValue(key string) string {
	a.settingsMu.RLock()
	value, ok := a.settingsCache[key]
	a.settingsMu.RUnlock()
	if ok && strings.TrimSpace(value) != "" {
		return value
	}
	for _, descriptor := range a.settingDescriptors() {
		if descriptor.Key == key {
			return a.settingDefault(descriptor)
		}
	}
	return ""
}

// reloadSettings refreshes the in-memory cache from the database.
func (a *app) reloadSettings() error {
	var rows []DBSystemSetting
	if err := a.db.Find(&rows).Error; err != nil {
		return err
	}
	next := make(map[string]string, len(rows))
	for _, row := range rows {
		next[row.Key] = row.Value
	}
	a.settingsMu.Lock()
	a.settingsCache = next
	a.settingsMu.Unlock()
	return nil
}

func (a *app) allowRegistration() bool {
	return a.settingValue(settingAllowRegistration) != "false"
}

func (a *app) workspaceName() string {
	return a.settingValue(settingWorkspaceName)
}

func (a *app) workspaceDescription() string {
	return a.settingValue(settingWorkspaceDescription)
}

// uploadFileLimit resolves the effective single-file ceiling. An out-of-range or
// unparsable stored value falls back to the deployment limit instead of widening it.
func (a *app) uploadFileLimit() int64 {
	parsed, err := strconv.ParseInt(a.settingValue(settingUploadFileLimit), 10, 64)
	if err != nil || parsed < minUploadFileLimit || parsed > a.cfg.UploadFileLimit {
		return a.cfg.UploadFileLimit
	}
	return parsed
}

func (a *app) settingTypedValue(descriptor settingDescriptor) any {
	switch descriptor.Kind {
	case settingKindBoolean:
		return a.settingValue(descriptor.Key) != "false"
	case settingKindBytes:
		parsed, err := strconv.ParseInt(a.settingValue(descriptor.Key), 10, 64)
		if err != nil {
			return a.settingDefault(descriptor)
		}
		return parsed
	default:
		return a.settingValue(descriptor.Key)
	}
}

// normalizeSettingRaw validates one incoming JSON value against its descriptor and
// returns the canonical stored representation.
func (a *app) normalizeSettingRaw(descriptor settingDescriptor, raw json.RawMessage) (string, error) {
	switch descriptor.Kind {
	case settingKindBoolean:
		var value bool
		if err := json.Unmarshal(raw, &value); err != nil {
			return "", errors.New(descriptor.Label + " 必须是布尔值")
		}
		if value {
			return "true", nil
		}
		return "false", nil
	case settingKindBytes:
		var value int64
		if err := json.Unmarshal(raw, &value); err != nil {
			return "", errors.New(descriptor.Label + " 必须是整数")
		}
		if value < descriptor.MinBytes {
			return "", errors.New(descriptor.Label + " 不能小于 " + strconv.FormatInt(descriptor.MinBytes, 10) + " 字节")
		}
		if descriptor.MaxBytes > 0 && value > descriptor.MaxBytes {
			return "", errors.New(descriptor.Label + " 不能超过部署上限 " + strconv.FormatInt(descriptor.MaxBytes, 10) + " 字节")
		}
		return strconv.FormatInt(value, 10), nil
	default:
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return "", errors.New(descriptor.Label + " 必须是文本")
		}
		value = strings.TrimSpace(value)
		if descriptor.Kind == settingKindString && value == "" {
			return "", errors.New(descriptor.Label + " 不能为空")
		}
		if len([]rune(value)) > descriptor.MaxLength {
			return "", errors.New(descriptor.Label + " 长度不能超过 " + strconv.Itoa(descriptor.MaxLength) + " 个字符")
		}
		if descriptor.Kind == settingKindString && strings.ContainsAny(value, "\r\n\t") {
			return "", errors.New(descriptor.Label + " 不能包含换行或制表符")
		}
		return value, nil
	}
}

func (a *app) settingItems() []gin.H {
	var rows []DBSystemSetting
	_ = a.db.Find(&rows).Error
	stored := make(map[string]DBSystemSetting, len(rows))
	for _, row := range rows {
		stored[row.Key] = row
	}
	items := make([]gin.H, 0, len(a.settingDescriptors()))
	for _, descriptor := range a.settingDescriptors() {
		fallback := a.settingDefault(descriptor)
		item := gin.H{
			"key":         descriptor.Key,
			"label":       descriptor.Label,
			"description": descriptor.Description,
			"kind":        descriptor.Kind,
			"value":       a.settingTypedValue(descriptor),
			"default":     fallback,
			"maxLength":   descriptor.MaxLength,
			"minBytes":    descriptor.MinBytes,
			"maxBytes":    descriptor.MaxBytes,
			"customized":  false,
		}
		if row, ok := stored[descriptor.Key]; ok {
			item["customized"] = row.Value != fallback
			item["updatedAt"] = row.UpdatedAt
			item["updatedBy"] = row.UpdatedBy
		}
		items = append(items, item)
	}
	return items
}

func (a *app) environmentInfo() gin.H {
	return gin.H{
		"environment":      a.cfg.Environment,
		"databaseDriver":   a.cfg.DBDriver,
		"dataDir":          a.cfg.DataDir,
		"meshServiceUrl":   a.cfg.MeshServiceURL,
		"workerCount":      a.cfg.WorkerCount,
		"uploadFileLimit":  a.cfg.UploadFileLimit,
		"uploadChunkLimit": a.cfg.UploadChunkLimit,
		"registerCodeSet":  strings.TrimSpace(a.cfg.RegisterCode) != "",
	}
}

func (a *app) getSettings(c *gin.Context) {
	ok(c, gin.H{
		"workspace":   gin.H{"name": a.workspaceName(), "description": a.workspaceDescription()},
		"items":       a.settingItems(),
		"environment": a.environmentInfo(),
	})
}

func (a *app) updateSettings(c *gin.Context) {
	var req struct {
		Values map[string]json.RawMessage `json:"values"`
	}
	if err := c.ShouldBindJSON(&req); err != nil || len(req.Values) == 0 {
		fail(c, 400, "配置参数不完整")
		return
	}
	descriptors := map[string]settingDescriptor{}
	for _, descriptor := range a.settingDescriptors() {
		descriptors[descriptor.Key] = descriptor
	}
	normalized := make(map[string]string, len(req.Values))
	for key, raw := range req.Values {
		descriptor, known := descriptors[key]
		if !known {
			fail(c, 400, "不支持的配置项: "+key)
			return
		}
		value, err := a.normalizeSettingRaw(descriptor, raw)
		if err != nil {
			fail(c, 400, err.Error())
			return
		}
		normalized[key] = value
	}
	now := time.Now()
	editor := userID(c)
	// All values are validated before the transaction so a rejected item cannot
	// leave the workspace with a partially applied configuration.
	if err := a.db.Transaction(func(tx *gorm.DB) error {
		for key, value := range normalized {
			row := DBSystemSetting{Key: key, Value: value, UpdatedAt: now, UpdatedBy: editor}
			if err := tx.Clauses(clause.OnConflict{
				Columns:   []clause.Column{{Name: "key"}},
				DoUpdates: clause.AssignmentColumns([]string{"value", "updated_at", "updated_by"}),
			}).Create(&row).Error; err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		fail(c, 500, "保存工作区配置失败")
		return
	}
	if err := a.reloadSettings(); err != nil {
		fail(c, 500, "刷新配置缓存失败")
		return
	}
	ok(c, gin.H{
		"workspace":   gin.H{"name": a.workspaceName(), "description": a.workspaceDescription()},
		"items":       a.settingItems(),
		"environment": a.environmentInfo(),
	})
}

// ---------------------------------------------------------------------------
// Runtime status and usage
// ---------------------------------------------------------------------------

type memberUsage struct {
	ProjectCount     int64
	AssetCount       int64
	AlignmentCount   int64
	MeasurementCount int64
}

func (a *app) memberUsage(ownerID int64) memberUsage {
	var usage memberUsage
	a.db.Model(&DBProject{}).Where("owner_id = ?", ownerID).Count(&usage.ProjectCount)
	a.db.Model(&DBAsset{}).Where("owner_id = ?", ownerID).Count(&usage.AssetCount)
	a.db.Model(&DBAlignment{}).Where("owner_id = ?", ownerID).Count(&usage.AlignmentCount)
	a.db.Model(&DBMeasurement{}).Where("owner_id = ?", ownerID).Count(&usage.MeasurementCount)
	return usage
}

func (a *app) meshServiceStatus() gin.H {
	client := &http.Client{Timeout: 1200 * time.Millisecond}
	started := time.Now()
	resp, err := client.Get(strings.TrimRight(a.cfg.MeshServiceURL, "/") + "/health")
	latency := time.Since(started).Milliseconds()
	if err != nil {
		return gin.H{"status": "unavailable", "url": a.cfg.MeshServiceURL, "latencyMs": latency}
	}
	defer resp.Body.Close()
	status := "unavailable"
	if resp.StatusCode < http.StatusMultipleChoices {
		status = "ok"
	}
	return gin.H{"status": status, "url": a.cfg.MeshServiceURL, "latencyMs": latency, "httpStatus": resp.StatusCode}
}

type assetTypeCount struct {
	Type  string `json:"type"`
	Total int64  `json:"total"`
}

func (a *app) systemInfo(c *gin.Context) {
	accountID := userID(c)
	role := a.currentRole(c)
	isAdmin := role == roleAdmin

	usage := a.memberUsage(accountID)

	var totalProjects, totalAssets, totalMembers, totalAlignments, totalMeasurements int64
	a.db.Model(&DBProject{}).Count(&totalProjects)
	a.db.Model(&DBAsset{}).Count(&totalAssets)
	a.db.Model(&DBUser{}).Count(&totalMembers)
	a.db.Model(&DBAlignment{}).Count(&totalAlignments)
	a.db.Model(&DBMeasurement{}).Count(&totalMeasurements)

	var sourceBytes, derivativeBytes, uploadBytes int64
	a.db.Model(&DBAsset{}).Select("COALESCE(SUM(source_size), 0)").Scan(&sourceBytes)
	a.db.Model(&DBAssetDerivative{}).Select("COALESCE(SUM(byte_size), 0)").Scan(&derivativeBytes)
	a.db.Model(&DBUpload{}).Where("status = ?", "uploading").Select("COALESCE(SUM(file_size), 0)").Scan(&uploadBytes)

	typeRows := []assetTypeCount{}
	a.db.Model(&DBAsset{}).Select("type, count(*) as total").Group("type").Order("type asc").Scan(&typeRows)
	statusRows := []assetTypeCount{}
	a.db.Model(&DBAsset{}).Select("status as type, count(*) as total").Group("status").Order("status asc").Scan(&statusRows)

	databaseStatus := "ready"
	if sqlDB, err := a.db.DB(); err != nil || sqlDB.PingContext(c.Request.Context()) != nil {
		databaseStatus = "unavailable"
	}

	data := gin.H{
		"service":           "cloudBIM-viewer backend",
		"environment":       a.cfg.Environment,
		"serverTime":        time.Now(),
		"startedAt":         a.startedAt,
		"uptimeSeconds":     int64(time.Since(a.startedAt).Seconds()),
		"database":          a.cfg.DBDriver,
		"databaseStatus":    databaseStatus,
		"storageConfigured": strings.TrimSpace(a.cfg.DataDir) != "",
		"projectCount":      usage.ProjectCount,
		"assetCount":        usage.AssetCount,
		"alignmentCount":    usage.AlignmentCount,
		"measurementCount":  usage.MeasurementCount,
		"role":              role,
		"roleLabel":         roleLabel(role),
		"member": gin.H{
			"id":          accountID,
			"role":        role,
			"permissions": a.permissionList(role),
		},
		"workspace": gin.H{
			"name":              a.workspaceName(),
			"description":       a.workspaceDescription(),
			"memberCount":       totalMembers,
			"adminCount":        a.countActiveAdmins(),
			"projectCount":      totalProjects,
			"assetCount":        totalAssets,
			"alignmentCount":    totalAlignments,
			"measurementCount":  totalMeasurements,
			"allowRegistration": a.allowRegistration(),
		},
		"storage": gin.H{
			"sourceBytes":        sourceBytes,
			"derivativeBytes":    derivativeBytes,
			"pendingUploadBytes": uploadBytes,
			"totalBytes":         sourceBytes + derivativeBytes,
		},
		"assets": gin.H{
			"total":       totalAssets,
			"byType":      typeRows,
			"byStatus":    statusRows,
			"workerCount": a.cfg.WorkerCount,
		},
		"meshService": a.meshServiceStatus(),
		"capabilities": []gin.H{
			{"key": "profile", "label": "账号资料", "description": "维护昵称、邮箱与联系电话。"},
			{"key": "password", "label": "密码与会话", "description": "修改密码并结束其他设备的登录状态。"},
			{"key": "members", "label": "成员与权限", "description": "管理工作区成员的加入状态与角色。"},
			{"key": "status", "label": "服务状态", "description": "查看数据库、存储与算法服务运行情况。"},
		},
	}
	if expires, exists := c.Get("sessionExpiresAt"); exists {
		data["sessionExpiresAt"] = expires
	}
	if isAdmin {
		data["dataDir"] = a.cfg.DataDir
	}
	ok(c, data)
}

func (a *app) permissionList(role string) []string {
	if normalizeUserRole(role) == roleAdmin {
		return []string{"workspace:read", "member:manage", "asset:manage"}
	}
	return []string{"workspace:read", "asset:manage"}
}

// ---------------------------------------------------------------------------
// Members and roles
// ---------------------------------------------------------------------------

func (a *app) memberResponse(account DBUser, viewerID int64, activeAdmins int64, includeContact bool) gin.H {
	usage := a.memberUsage(account.ID)
	role := normalizeUserRole(account.Role)
	status := normalizeUserStatus(account.Status)
	item := gin.H{
		"id":               account.ID,
		"username":         account.Username,
		"displayName":      account.DisplayName,
		"role":             role,
		"roleLabel":        roleLabel(role),
		"status":           status,
		"createdAt":        account.CreatedAt,
		"updatedAt":        account.UpdatedAt,
		"lastLoginAt":      account.LastLoginAt,
		"projectCount":     usage.ProjectCount,
		"assetCount":       usage.AssetCount,
		"alignmentCount":   usage.AlignmentCount,
		"measurementCount": usage.MeasurementCount,
		"isSelf":           account.ID == viewerID,
		"isLastAdmin":      role == roleAdmin && status == userStatusActive && activeAdmins <= 1,
	}
	if includeContact {
		item["email"] = account.Email
		item["phone"] = account.Phone
	}
	return item
}

func (a *app) listMembers(c *gin.Context) {
	viewer := userID(c)
	isAdmin := a.currentRole(c) == roleAdmin
	var accounts []DBUser
	query := a.db.Order("created_at asc, id asc")
	if !isAdmin {
		// Members only ever see their own record; the workspace roster is
		// administrative information.
		query = query.Where("id = ?", viewer)
	}
	if err := query.Find(&accounts).Error; err != nil {
		fail(c, 500, "查询成员失败")
		return
	}
	activeAdmins := a.countActiveAdmins()
	list := make([]gin.H, 0, len(accounts))
	for _, account := range accounts {
		list = append(list, a.memberResponse(account, viewer, activeAdmins, isAdmin))
	}
	// Administrators are listed first so the roster reads by responsibility.
	for i := 1; i < len(list); i++ {
		for j := i; j > 0 && list[j]["role"] == roleAdmin && list[j-1]["role"] != roleAdmin; j-- {
			list[j], list[j-1] = list[j-1], list[j]
		}
	}
	ok(c, gin.H{
		"total":            len(list),
		"list":             list,
		"canManage":        isAdmin,
		"activeAdminCount": activeAdmins,
		"viewerId":         viewer,
	})
}

func (a *app) updateMember(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, 400, "成员 ID 无效")
		return
	}
	if id == userID(c) {
		fail(c, 400, "不能修改自己的角色或状态")
		return
	}
	var req struct {
		Role   *string `json:"role"`
		Status *string `json:"status"`
	}
	if err := c.ShouldBindJSON(&req); err != nil || (req.Role == nil && req.Status == nil) {
		fail(c, 400, "成员变更参数不完整")
		return
	}
	var target DBUser
	if err := a.db.First(&target, id).Error; err != nil {
		fail(c, 404, "成员不存在")
		return
	}
	nextRole := normalizeUserRole(target.Role)
	nextStatus := normalizeUserStatus(target.Status)
	if req.Role != nil {
		value := strings.ToLower(strings.TrimSpace(*req.Role))
		if value != roleAdmin && value != roleMember {
			fail(c, 400, "角色只能是 admin 或 member")
			return
		}
		nextRole = value
	}
	if req.Status != nil {
		value := strings.ToLower(strings.TrimSpace(*req.Status))
		if value != userStatusActive && value != userStatusDisabled {
			fail(c, 400, "状态只能是 active 或 disabled")
			return
		}
		nextStatus = value
	}
	// The workspace must keep one usable administrator at all times.
	activeAdmins := a.countActiveAdmins()
	targetIsActiveAdmin := normalizeUserRole(target.Role) == roleAdmin && normalizeUserStatus(target.Status) == userStatusActive
	losesAdmin := nextRole != roleAdmin || nextStatus != userStatusActive
	if targetIsActiveAdmin && losesAdmin && activeAdmins <= 1 {
		fail(c, 409, "工作区至少需要保留一名启用的管理员")
		return
	}
	now := time.Now()
	updates := map[string]any{"role": nextRole, "status": nextStatus, "updated_at": now}
	if err := a.db.Model(&DBUser{}).Where("id = ?", target.ID).Updates(updates).Error; err != nil {
		fail(c, 500, "保存成员变更失败")
		return
	}
	// Disabling an account must end its active sessions immediately; role changes
	// are read from the database on every request and need no extra step.
	if nextStatus == userStatusDisabled && normalizeUserStatus(target.Status) != userStatusDisabled {
		if err := a.revokeSessions(c, target.ID); err != nil {
			fail(c, 500, "结束该成员会话失败")
			return
		}
	}
	target.Role, target.Status, target.UpdatedAt = nextRole, nextStatus, now
	ok(c, a.memberResponse(target, userID(c), a.countActiveAdmins(), true))
}

func (a *app) deleteMember(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, 400, "成员 ID 无效")
		return
	}
	if id == userID(c) {
		fail(c, 400, "不能删除当前登录账号")
		return
	}
	var target DBUser
	if err := a.db.First(&target, id).Error; err != nil {
		fail(c, 404, "成员不存在")
		return
	}
	if normalizeUserRole(target.Role) == roleAdmin && normalizeUserStatus(target.Status) == userStatusActive && a.countActiveAdmins() <= 1 {
		fail(c, 409, "工作区至少需要保留一名启用的管理员")
		return
	}
	// Assets carry on-disk artifacts; refuse to remove a member that still owns
	// work so the workspace cannot be left with orphaned data.
	usage := a.memberUsage(target.ID)
	if usage.ProjectCount+usage.AssetCount+usage.AlignmentCount+usage.MeasurementCount > 0 {
		fail(c, 409, "该成员仍有项目或资产数据，请先移交或清理后再删除")
		return
	}
	var pendingUploads int64
	a.db.Model(&DBUpload{}).Where("owner_id = ?", target.ID).Count(&pendingUploads)
	if pendingUploads > 0 {
		fail(c, 409, "该成员仍有未完成的上传任务，请先处理后再删除")
		return
	}
	if err := a.db.Delete(&DBUser{}, target.ID).Error; err != nil {
		fail(c, 500, "删除成员失败")
		return
	}
	ok(c, gin.H{"id": target.ID, "username": target.Username, "deletedAt": time.Now()})
}
