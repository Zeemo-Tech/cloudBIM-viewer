package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// DBInspectionAction is an append-only action record. It never changes the
// immutable report, measured geometry, acceptance result, or a bar's status.
// Demonstration and real actions have separate unique keys and progress chains.
type DBInspectionAction struct {
	ID            int64     `json:"id" gorm:"primaryKey"`
	RunID         int64     `json:"-" gorm:"not null;uniqueIndex:idx_inspection_action_step;index"`
	OwnerID       int64     `json:"-" gorm:"not null;index"`
	ActorID       int64     `json:"actorId" gorm:"index;not null;default:0"`
	ResultVersion string    `json:"resultVersion" gorm:"size:64;not null;index"`
	IFCGlobalID   string    `json:"ifcGlobalId" gorm:"size:255;not null;uniqueIndex:idx_inspection_action_step"`
	Action        string    `json:"action" gorm:"size:32;not null;uniqueIndex:idx_inspection_action_step"`
	Note          string    `json:"note" gorm:"type:text;not null"`
	Demonstration bool      `json:"demonstration" gorm:"not null;uniqueIndex:idx_inspection_action_step"`
	CreatedAt     time.Time `json:"createdAt" gorm:"autoCreateTime"`
}

type inspectionActionRequest struct {
	IFCGlobalID   string `json:"ifcGlobalId"`
	Action        string `json:"action"`
	Note          string `json:"note"`
	Demonstration bool   `json:"demonstration"`
}

type inspectionActionError struct {
	status int
	msg    string
}

func (e *inspectionActionError) Error() string { return e.msg }

// SQLite has no SELECT FOR UPDATE. Serializing these short write transactions
// in-process supplies the same ordering for SQLite tests/local use; the unique
// index also prevents duplicate events across connections and processes.
var inspectionActionSQLiteMu sync.Mutex

func (a *app) listInspectionActions(c *gin.Context) {
	a.listInspectionActionsForOwner(c, userID(c))
}

func (a *app) listInspectionActionsForOwner(c *gin.Context, ownerID int64) {
	var run DBC2MReportRun
	if err := a.db.WithContext(c.Request.Context()).Where("owner_id = ? AND result_version = ?", ownerID, c.Param("version")).First(&run).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			fail(c, http.StatusNotFound, "C2M 报告不存在")
		} else {
			fail(c, http.StatusInternalServerError, "查询处置记录失败")
		}
		return
	}
	items := make([]DBInspectionAction, 0)
	if err := a.db.WithContext(c.Request.Context()).Where("owner_id = ? AND run_id = ?", ownerID, run.ID).Order("id ASC").Find(&items).Error; err != nil {
		fail(c, http.StatusInternalServerError, "查询处置记录失败")
		return
	}
	ok(c, gin.H{"items": items})
}

func (a *app) createInspectionAction(c *gin.Context) {
	a.createInspectionActionForOwner(c, func(_ *gorm.DB) (int64, error) { return userID(c), nil })
}

// resolveOwner is called inside the write transaction, before the shared parent
// row lock. Operator routes validate the current assignment without impersonating
// the report owner; actor identity always remains the authenticated account.
func (a *app) createInspectionActionForOwner(c *gin.Context, resolveOwner func(*gorm.DB) (int64, error)) {
	var req inspectionActionRequest
	decoder := json.NewDecoder(http.MaxBytesReader(c.Writer, c.Request.Body, 32<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		fail(c, http.StatusBadRequest, "处置参数无效")
		return
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		fail(c, http.StatusBadRequest, "处置参数无效")
		return
	}
	req.IFCGlobalID = strings.TrimSpace(req.IFCGlobalID)
	req.Note = strings.TrimSpace(req.Note)
	if req.IFCGlobalID == "" || utf8.RuneCountInString(req.IFCGlobalID) > 255 || utf8.RuneCountInString(req.Note) > 4000 {
		fail(c, http.StatusBadRequest, "钢筋编号或处置说明无效")
		return
	}
	steps := []string{"acknowledge", "record_adjustment", "request_recheck"}
	validAction := false
	for _, step := range steps {
		validAction = validAction || req.Action == step
	}
	if !validAction {
		fail(c, http.StatusBadRequest, "仅允许确认问题、登记调整和申请复检")
		return
	}
	if req.Action == "record_adjustment" && req.Note == "" {
		fail(c, http.StatusBadRequest, "登记调整必须填写处置说明，缺测钢筋可登记补扫安排")
		return
	}
	if a.db.Dialector.Name() == "sqlite" {
		inspectionActionSQLiteMu.Lock()
		defer inspectionActionSQLiteMu.Unlock()
	}
	var item DBInspectionAction
	err := a.db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
		ownerID, err := resolveOwner(tx)
		if err != nil {
			return err
		}
		var run DBC2MReportRun
		// Lock the parent row, which exists even when this bar has no events yet.
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("owner_id = ? AND result_version = ?", ownerID, c.Param("version")).First(&run).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return &inspectionActionError{http.StatusNotFound, "C2M 报告不存在"}
			}
			return err
		}
		var bar DBC2MReportBar
		if err := tx.Where("run_id = ? AND ifc_global_id = ?", run.ID, req.IFCGlobalID).First(&bar).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return &inspectionActionError{http.StatusNotFound, "报告中不存在该钢筋"}
			}
			return err
		}
		var previous []DBInspectionAction
		if err := tx.Where("owner_id = ? AND run_id = ? AND ifc_global_id = ? AND demonstration = ?", ownerID, run.ID, req.IFCGlobalID, req.Demonstration).Order("id ASC").Find(&previous).Error; err != nil {
			return err
		}
		if len(previous) >= len(steps) {
			return &inspectionActionError{http.StatusConflict, "已申请复检，请等待新的检测报告"}
		}
		for i, event := range previous {
			if event.Action != steps[i] {
				return &inspectionActionError{http.StatusConflict, "已有处置顺序异常，请核查记录"}
			}
		}
		if req.Action != steps[len(previous)] {
			return &inspectionActionError{http.StatusConflict, "须依次确认问题、登记调整、申请复检，不可跳步或重复"}
		}
		item = DBInspectionAction{RunID: run.ID, OwnerID: ownerID, ActorID: userID(c), ResultVersion: run.ResultVersion, IFCGlobalID: req.IFCGlobalID, Action: req.Action, Note: req.Note, Demonstration: req.Demonstration}
		return tx.Create(&item).Error
	})
	if err != nil {
		var actionErr *inspectionActionError
		switch {
		case errors.As(err, &actionErr):
			fail(c, actionErr.status, actionErr.msg)
		case errors.Is(err, gorm.ErrDuplicatedKey):
			fail(c, http.StatusConflict, "该处置动作已登记，请刷新记录")
		default:
			fail(c, http.StatusInternalServerError, "保存处置记录失败")
		}
		return
	}
	created(c, item)
}
