package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// Closed allowlist: granting the operator role never grants the legacy business
// APIs. Every operator handler separately reloads the account and assignment.
func operatorRouteAllowed(method, path string) bool {
	switch method + " " + path {
	case "GET /auth/me", "PATCH /auth/profile", "POST /auth/password", "POST /auth/sessions/revoke", "POST /auth/logout", "GET /operator/tasks":
		return true
	}
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) != 4 || parts[0] != "operator" {
		return false
	}
	if parts[1] == "tasks" && method == http.MethodGet {
		return parts[3] == "result" || parts[3] == "geometry"
	}
	return parts[1] == "reports" && parts[3] == "actions" && (method == http.MethodGet || method == http.MethodPost)
}

func (a *app) validateOperatorAssignment(adminID, projectID int64) error {
	if projectID <= 0 {
		return gorm.ErrRecordNotFound
	}
	var project DBProject
	return a.db.Where("id = ? AND owner_id = ?", projectID, adminID).First(&project).Error
}

func (a *app) createOperatorMember(c *gin.Context) {
	if a.currentRole(c) != roleAdmin {
		fail(c, 403, "仅管理员可创建操作员")
		return
	}
	var req struct {
		Username          string `json:"username"`
		Password          string `json:"password"`
		DisplayName       string `json:"displayName"`
		OperatorProjectID *int64 `json:"operatorProjectId"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(c.Writer, c.Request.Body, 32<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		fail(c, 400, "操作员参数无效")
		return
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		fail(c, 400, "操作员参数无效")
		return
	}
	req.Username, req.DisplayName = strings.TrimSpace(req.Username), strings.TrimSpace(req.DisplayName)
	if req.Username == "" || utf8.RuneCountInString(req.Username) > 128 || utf8.RuneCountInString(req.DisplayName) > 128 || len(req.Password) < 6 || len(req.Password) > 72 {
		fail(c, 400, "用户名、显示名称或密码格式不正确")
		return
	}
	if req.OperatorProjectID != nil && a.validateOperatorAssignment(userID(c), *req.OperatorProjectID) != nil {
		fail(c, 400, "只能分配管理员本人拥有的项目")
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	var existing DBUser
	if err := a.db.Where("lower(username) = lower(?)", req.Username).First(&existing).Error; err == nil {
		fail(c, 409, "用户名已存在")
		return
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, 500, "查询账号失败")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		fail(c, 500, "生成密码摘要失败")
		return
	}
	account := DBUser{Username: req.Username, PasswordHash: string(hash), DisplayName: req.DisplayName, Role: roleOperator, Status: userStatusActive, OperatorProjectID: req.OperatorProjectID, CreatedAt: time.Now()}
	if err := a.db.Create(&account).Error; err != nil {
		if errors.Is(err, gorm.ErrDuplicatedKey) {
			fail(c, 409, "用户名已存在")
		} else {
			fail(c, 500, "创建操作员失败")
		}
		return
	}
	created(c, a.memberResponse(account, userID(c), a.countActiveAdmins(), true))
}

func operatorScopeError() error {
	return &inspectionActionError{http.StatusNotFound, "已分配的检测任务或报告不存在"}
}

func operatorAccount(db *gorm.DB, actorID int64) (DBUser, error) {
	var account DBUser
	if actorID <= 0 || db.First(&account, actorID).Error != nil || normalizeUserStatus(account.Status) != userStatusActive {
		return account, operatorScopeError()
	}
	return account, nil
}

// Resolve owner only after validating the exact task/project pair. Never rewrite
// Gin's userID or expand any existing owner-scoped query.
func operatorPair(db *gorm.DB, actorID, scanID, bimID int64) (DBProject, DBAsset, DBAsset, error) {
	var project DBProject
	var scan, bim DBAsset
	account, err := operatorAccount(db, actorID)
	if err != nil {
		return project, scan, bim, err
	}
	if db.Where("id = ? AND type = ?", scanID, "pointcloud").First(&scan).Error != nil || scan.ProjectID <= 0 {
		return project, scan, bim, operatorScopeError()
	}
	projectQuery := db.Where("id = ?", scan.ProjectID)
	if normalizeUserRole(account.Role) == roleOperator {
		if account.OperatorProjectID == nil || *account.OperatorProjectID != scan.ProjectID {
			return project, scan, bim, operatorScopeError()
		}
	} else {
		projectQuery = projectQuery.Where("owner_id = ?", actorID)
	}
	if projectQuery.First(&project).Error != nil || scan.OwnerID != project.OwnerID || scan.LinkedBimID == nil || *scan.LinkedBimID != bimID {
		return project, scan, bim, operatorScopeError()
	}
	if db.Where("id = ? AND owner_id = ? AND project_id = ? AND type = ?", bimID, project.OwnerID, project.ID, "bim").First(&bim).Error != nil {
		return project, scan, bim, operatorScopeError()
	}
	return project, scan, bim, nil
}

func operatorFailure(c *gin.Context, err error) {
	var scoped *inspectionActionError
	if errors.As(err, &scoped) {
		fail(c, scoped.status, scoped.msg)
	} else {
		fail(c, 500, "查询检测任务失败")
	}
}

func (a *app) operatorTasks(c *gin.Context) {
	db := a.db.WithContext(c.Request.Context())
	account, err := operatorAccount(db, userID(c))
	if err != nil {
		operatorFailure(c, err)
		return
	}
	projects := make([]DBProject, 0)
	query := db.Where("owner_id = ?", account.ID)
	if normalizeUserRole(account.Role) == roleOperator {
		if account.OperatorProjectID == nil {
			ok(c, gin.H{"items": []gin.H{}, "scanner": gin.H{"connected": false, "reason": "扫描设备尚未接入"}})
			return
		}
		query = db.Where("id = ?", *account.OperatorProjectID)
	}
	if err := query.Order("id ASC").Find(&projects).Error; err != nil {
		operatorFailure(c, err)
		return
	}
	items := make([]gin.H, 0)
	for _, project := range projects {
		var scans []DBAsset
		if err := db.Where("project_id = ? AND owner_id = ? AND type = ?", project.ID, project.OwnerID, "pointcloud").Order("created_at DESC, id DESC").Find(&scans).Error; err != nil {
			operatorFailure(c, err)
			return
		}
		for _, scan := range scans {
			item := gin.H{"projectId": project.ID, "projectName": project.Name, "scanId": scan.ID, "bimId": nil, "scanName": scan.SourceName, "bimName": "", "scanStatus": scan.Status, "hasResult": false}
			if scan.LinkedBimID != nil {
				var bim DBAsset
				if db.Where("id = ? AND owner_id = ? AND project_id = ? AND type = ?", *scan.LinkedBimID, project.OwnerID, project.ID, "bim").First(&bim).Error == nil {
					item["bimId"], item["bimName"] = bim.ID, bim.SourceName
					var row DBC2MResult
					if db.Where("owner_id = ? AND scan_id = ? AND bim_id = ?", project.OwnerID, scan.ID, bim.ID).First(&row).Error == nil {
						item["hasResult"], item["resultVersion"] = true, c2mResultVersion(row)
					}
				}
			}
			items = append(items, item)
		}
	}
	ok(c, gin.H{"items": items, "scanner": gin.H{"connected": false, "reason": "扫描设备尚未接入"}})
}

func (a *app) operatorCurrentResult(c *gin.Context) (DBC2MResult, bool) {
	var row DBC2MResult
	scanID, scanErr := strconv.ParseInt(c.Param("scanId"), 10, 64)
	bimID, bimErr := strconv.ParseInt(c.Query("bimId"), 10, 64)
	if scanErr != nil || bimErr != nil || scanID <= 0 || bimID <= 0 {
		fail(c, 400, "扫描任务和 BIM ID 无效")
		return row, false
	}
	db := a.db.WithContext(c.Request.Context())
	project, _, _, err := operatorPair(db, userID(c), scanID, bimID)
	if err != nil {
		operatorFailure(c, err)
		return row, false
	}
	if err := db.Where("owner_id = ? AND scan_id = ? AND bim_id = ?", project.OwnerID, scanID, bimID).First(&row).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			fail(c, 404, "暂无检测结果")
		} else {
			operatorFailure(c, err)
		}
		return row, false
	}
	return row, true
}

func (a *app) operatorResult(c *gin.Context) {
	row, valid := a.operatorCurrentResult(c)
	if valid {
		ok(c, a.c2mResultResponse(row))
	}
}

func (a *app) operatorGeometry(c *gin.Context) {
	row, valid := a.operatorCurrentResult(c)
	if !valid {
		return
	}
	version := strings.TrimSpace(c.Query("version"))
	if version == "" {
		fail(c, 400, "必须指定检测结果版本")
		return
	}
	if version != c2mResultVersion(row) {
		fail(c, 409, "检测结果版本已变化，请刷新后重试")
		return
	}
	if fresh, reason := a.c2mFreshness(row); !fresh {
		fail(c, 409, reason)
		return
	}
	path, err := a.c2mArtifactPath(row.ColoredPlyPath)
	if err != nil || !regularFileExists(path) {
		fail(c, 404, "检测几何文件不存在")
		return
	}
	c.Header("Content-Disposition", `inline; filename="c2m_colored.ply"`)
	c.Header("Cache-Control", "private, no-store")
	c.Header("X-C2M-Result-Version", version)
	c.File(path)
}

func operatorReportOwner(db *gorm.DB, actorID int64, version string) (int64, error) {
	var run DBC2MReportRun
	if db.Where("result_version = ?", version).First(&run).Error != nil {
		return 0, operatorScopeError()
	}
	project, _, _, err := operatorPair(db, actorID, run.ScanID, run.BimID)
	if err != nil {
		return 0, err
	}
	if run.OwnerID != project.OwnerID {
		return 0, operatorScopeError()
	}
	return run.OwnerID, nil
}

func (a *app) operatorListActions(c *gin.Context) {
	ownerID, err := operatorReportOwner(a.db.WithContext(c.Request.Context()), userID(c), c.Param("version"))
	if err != nil {
		operatorFailure(c, err)
		return
	}
	a.listInspectionActionsForOwner(c, ownerID)
}

func (a *app) operatorCreateAction(c *gin.Context) {
	a.createInspectionActionForOwner(c, func(tx *gorm.DB) (int64, error) {
		ownerID, err := operatorReportOwner(tx, userID(c), c.Param("version"))
		if err != nil {
			return 0, err
		}
		var run DBC2MReportRun
		if err := tx.Where("owner_id = ? AND result_version = ?", ownerID, c.Param("version")).First(&run).Error; err != nil {
			return 0, err
		}
		// Lock the current result before locking its report action chain. A new
		// computation cannot replace the result during this action transaction.
		var current DBC2MResult
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("owner_id = ? AND scan_id = ? AND bim_id = ?", ownerID, run.ScanID, run.BimID).First(&current).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return 0, &inspectionActionError{http.StatusConflict, "检测结果已移除，请等待新的检测报告"}
			}
			return 0, err
		}
		if c2mResultVersion(current) != run.ResultVersion {
			return 0, &inspectionActionError{http.StatusConflict, "检测结果已更新，请刷新后处置"}
		}
		// Freshness must read through the same transaction as the version guard.
		// This narrow app shares only configuration and the transaction handle;
		// it never changes the authenticated actor or copies mutex state.
		scoped := &app{db: tx, cfg: a.cfg}
		if fresh, reason := scoped.c2mFreshness(current); !fresh {
			return 0, &inspectionActionError{http.StatusConflict, reason}
		}
		return ownerID, nil
	})
}
