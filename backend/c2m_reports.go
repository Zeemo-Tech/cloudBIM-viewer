package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

// DBC2MReportRun is an immutable, metadata-only audit snapshot. Geometry artifacts
// intentionally remain owned by DBC2MResult and may be cleaned up later.
type DBC2MReportRun struct {
	ID               int64     `json:"id" gorm:"primaryKey"`
	OwnerID          int64     `json:"ownerId" gorm:"index:idx_c2m_report_owner_scan_bim;not null"`
	ScanID           int64     `json:"scanId" gorm:"index:idx_c2m_report_owner_scan_bim;not null"`
	BimID            int64     `json:"bimId" gorm:"index:idx_c2m_report_owner_scan_bim;not null"`
	ResultVersion    string    `json:"resultVersion" gorm:"size:64;uniqueIndex;not null"`
	InputFingerprint string    `json:"inputFingerprint" gorm:"size:64;index"`
	ParamsJSON       string    `json:"paramsJson" gorm:"type:text"`
	TransformJSON    string    `json:"transformJson" gorm:"type:text"`
	AlgorithmVersion string    `json:"algorithmVersion" gorm:"size:128"`
	Profile          string    `json:"profile" gorm:"size:32"`
	MetricDirection  string    `json:"metricDirection" gorm:"size:128"`
	DiagnosticsJSON  string    `json:"-" gorm:"type:text"`
	TimingsJSON      string    `json:"timingsJson" gorm:"type:text"`
	CreatedAt        time.Time `json:"createdAt" gorm:"autoCreateTime"`
}

type DBC2MReportBar struct {
	ID                      int64    `json:"id" gorm:"primaryKey"`
	RunID                   int64    `json:"runId" gorm:"index:idx_c2m_report_bar_run;not null"`
	IFCGlobalID             string   `json:"ifcGlobalId" gorm:"size:255;index:idx_c2m_report_bar_ifc;not null"`
	Status                  string   `json:"status" gorm:"size:32;index:idx_c2m_report_bar_status;not null"`
	DesignBarID             string   `json:"designBarId" gorm:"size:255"`
	Name                    string   `json:"name" gorm:"size:512"`
	MaxAbs                  *float64 `json:"maxAbs"`
	MeanAbs                 *float64 `json:"meanAbs"`
	P95Abs                  *float64 `json:"p95Abs"`
	RMSE                    *float64 `json:"rmse"`
	Coverage                *float64 `json:"coverage"`
	Bending                 *float64 `json:"curvatureMInv"` // curvatureMInv, retained for generic numeric queries
	MaxCentrelineDepartureM *float64 `json:"maxCentrelineDepartureM"`
	ResidualBowM            *float64 `json:"residualBowM"`
	RawJSON                 string   `json:"rawJson" gorm:"type:text"`
}

func reportNumber(v any) *float64 {
	n, ok := v.(float64)
	if !ok || math.IsNaN(n) || math.IsInf(n, 0) {
		return nil
	}
	return &n
}
func reportString(v any) string { s, _ := v.(string); return s }
func reportMetric(stats map[string]any, names ...string) *float64 {
	for _, name := range names {
		if v := reportNumber(stats[name]); v != nil {
			return v
		}
	}
	return nil
}

func (a *app) createC2MReportSnapshot(tx *gorm.DB, row *DBC2MResult) error {
	version := c2mResultVersion(*row)
	// Capture the matrix actually sent to geometry. Re-reading today's alignment
	// would give a historical snapshot the wrong transform during concurrent edits.
	transform := ""
	var parameters map[string]json.RawMessage
	if json.Unmarshal([]byte(row.ParamsJSON), &parameters) == nil {
		if matrix := parameters["alignmentMatrix"]; len(matrix) > 0 {
			transform = string(matrix)
		}
	}
	var diagnostic map[string]any
	_ = json.Unmarshal([]byte(row.DiagnosticsJSON), &diagnostic)
	timings := ""
	if diagnostic != nil {
		comparison, _ := diagnostic["rebarComparison"].(map[string]any)
		if v, ok := comparison["timings"]; ok {
			b, _ := json.Marshal(v)
			timings = string(b)
		}
	}
	run := DBC2MReportRun{OwnerID: row.OwnerID, ScanID: row.ScanID, BimID: row.BimID, ResultVersion: version, InputFingerprint: row.InputFingerprint, ParamsJSON: row.ParamsJSON, TransformJSON: transform, AlgorithmVersion: row.AlgorithmVersion, Profile: row.Profile, MetricDirection: row.MetricDirection, DiagnosticsJSON: row.DiagnosticsJSON, TimingsJSON: timings}
	if err := tx.Create(&run).Error; err != nil {
		return err
	}
	comparison, _ := diagnostic["rebarComparison"].(map[string]any)
	bars, _ := comparison["bars"].([]any)
	for _, raw := range bars {
		bar, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		ifc, status := reportString(bar["ifcGlobalId"]), reportString(bar["status"])
		if ifc == "" || status == "" {
			continue
		}
		stats, _ := bar["stats"].(map[string]any)
		if stats == nil {
			stats = map[string]any{}
		}
		measurement, _ := bar["measurement"].(map[string]any)
		surface, _ := measurement["surface"].(map[string]any)
		bending, _ := measurement["bending"].(map[string]any)
		maxAbs := reportMetric(surface, "maxAbs")
		if maxAbs == nil {
			maxAbs = reportMetric(stats, "maxAbs")
		}
		if maxAbs == nil {
			if v := reportMetric(stats, "max"); v != nil {
				n := math.Abs(*v)
				if minimum := reportMetric(stats, "min"); minimum != nil {
					n = math.Max(n, math.Abs(*minimum))
				}
				maxAbs = &n
			}
		}
		known, total := reportMetric(bar, "knownCount"), reportMetric(bar, "vertexCount")
		coverage := (*float64)(nil)
		if known != nil && total != nil && *total > 0 {
			n := *known / *total
			coverage = &n
		}
		encoded, _ := json.Marshal(bar)
		entry := DBC2MReportBar{RunID: run.ID, IFCGlobalID: ifc, Status: status, DesignBarID: reportString(bar["designBarId"]), Name: reportString(bar["name"]), MaxAbs: maxAbs, MeanAbs: reportMetric(stats, "meanAbs"), P95Abs: reportMetric(stats, "p95Abs"), RMSE: reportMetric(stats, "rmse", "RMSE"), Coverage: coverage, Bending: reportMetric(bending, "curvatureMInv", "estimateMInv"), MaxCentrelineDepartureM: reportMetric(bending, "maxCentrelineDepartureM"), ResidualBowM: reportMetric(bending, "residualBowM"), RawJSON: string(encoded)}
		if err := tx.Create(&entry).Error; err != nil {
			return err
		}
	}
	return nil
}

func reportPage(c *gin.Context) (int, int, bool) {
	page, size := 1, 50
	var err error
	if c.Query("page") != "" {
		page, err = strconv.Atoi(c.Query("page"))
		if err != nil || page < 1 || page > 1000000 {
			return 0, 0, false
		}
	}
	if c.Query("pageSize") != "" {
		size, err = strconv.Atoi(c.Query("pageSize"))
		if err != nil || size < 1 || size > 200 {
			return 0, 0, false
		}
	}
	return page, size, true
}
func (a *app) listC2MReports(c *gin.Context) {
	scan, e1 := strconv.ParseInt(c.Query("modelScanFileId"), 10, 64)
	bim, e2 := strconv.ParseInt(c.Query("modelBimFileId"), 10, 64)
	page, size, valid := reportPage(c)
	if e1 != nil || e2 != nil || scan <= 0 || bim <= 0 || !valid {
		fail(c, 400, "modelScanFileId、modelBimFileId 和分页参数无效")
		return
	}
	q := a.db.Where("owner_id = ? AND scan_id = ? AND bim_id = ?", userID(c), scan, bim)
	var total int64
	if err := q.Model(&DBC2MReportRun{}).Count(&total).Error; err != nil {
		fail(c, 500, "查询 C2M 报告失败")
		return
	}
	rows := make([]DBC2MReportRun, 0)
	if err := q.Order("created_at DESC, id DESC").Offset((page - 1) * size).Limit(size).Find(&rows).Error; err != nil {
		fail(c, 500, "查询 C2M 报告失败")
		return
	}
	ok(c, gin.H{"items": rows, "page": page, "pageSize": size, "total": total})
}
func (a *app) getC2MReport(c *gin.Context) {
	page, size, valid := reportPage(c)
	if !valid {
		fail(c, 400, "分页参数无效")
		return
	}
	var run DBC2MReportRun
	if err := a.db.Where("owner_id = ? AND result_version = ?", userID(c), c.Param("version")).First(&run).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			fail(c, 404, "C2M 报告不存在")
		} else {
			fail(c, 500, "查询 C2M 报告失败")
		}
		return
	}
	q := a.db.Where("run_id = ?", run.ID)
	if v := strings.TrimSpace(c.Query("ifcGlobalId")); v != "" {
		q = q.Where("ifc_global_id = ?", v)
	}
	if v := strings.TrimSpace(c.Query("status")); v != "" {
		q = q.Where("status = ?", v)
	}
	var total int64
	if err := q.Model(&DBC2MReportBar{}).Count(&total).Error; err != nil {
		fail(c, 500, "查询 C2M 报告钢筋失败")
		return
	}
	bars := make([]DBC2MReportBar, 0)
	if err := q.Order("id").Offset((page - 1) * size).Limit(size).Find(&bars).Error; err != nil {
		fail(c, http.StatusInternalServerError, "查询 C2M 报告钢筋失败")
		return
	}
	ok(c, gin.H{"run": run, "bars": bars, "page": page, "pageSize": size, "total": total})
}

type c2mStandardReportSource struct {
	ModelScanFileID int64 `json:"modelScanFileId"`
	ModelBimFileID  int64 `json:"modelBimFileId"`
}

type c2mStandardReport struct {
	Schema                      string                  `json:"schema"`
	Source                      c2mStandardReportSource `json:"source"`
	ResultVersion               string                  `json:"resultVersion"`
	CreatedAt                   time.Time               `json:"createdAt"`
	AlgorithmVersion            string                  `json:"algorithmVersion"`
	Profile                     string                  `json:"profile"`
	MetricDirection             string                  `json:"metricDirection"`
	InputFingerprint            string                  `json:"inputFingerprint"`
	AlignmentMatrix             json.RawMessage         `json:"alignmentMatrix"`
	Parameters                  json.RawMessage         `json:"parameters"`
	Inspection                  json.RawMessage         `json:"inspection"`
	InspectionUnavailableReason string                  `json:"inspectionUnavailableReason,omitempty"`
	Comparison                  json.RawMessage         `json:"comparison"`
	Diagnostics                 json.RawMessage         `json:"diagnostics"`
}

func rawReportJSON(value, empty string) (json.RawMessage, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return json.RawMessage(empty), nil
	}
	if !json.Valid([]byte(value)) {
		return nil, errors.New("stored report JSON is invalid")
	}
	return json.RawMessage(value), nil
}

func transientC2MReportRun(row DBC2MResult) DBC2MReportRun {
	transform := ""
	var parameters map[string]json.RawMessage
	if json.Unmarshal([]byte(row.ParamsJSON), &parameters) == nil {
		transform = string(parameters["alignmentMatrix"])
	}
	return DBC2MReportRun{
		OwnerID: row.OwnerID, ScanID: row.ScanID, BimID: row.BimID,
		ResultVersion: c2mResultVersion(row), InputFingerprint: row.InputFingerprint,
		ParamsJSON: row.ParamsJSON, TransformJSON: transform, AlgorithmVersion: row.AlgorithmVersion,
		Profile: row.Profile, MetricDirection: row.MetricDirection, DiagnosticsJSON: row.DiagnosticsJSON,
		CreatedAt: row.CreatedAt,
	}
}

func (a *app) findC2MReportRun(ownerID int64, version string) (DBC2MReportRun, error) {
	var run DBC2MReportRun
	err := a.db.Where("owner_id = ? AND result_version = ?", ownerID, version).First(&run).Error
	if err == nil || !errors.Is(err, gorm.ErrRecordNotFound) {
		return run, err
	}
	// Results created before report snapshots were introduced remain exportable
	// while they are the current result for the owner.
	var rows []DBC2MResult
	if err := a.db.Where("owner_id = ?", ownerID).Find(&rows).Error; err != nil {
		return run, err
	}
	for _, row := range rows {
		if c2mResultVersion(row) == version {
			return transientC2MReportRun(row), nil
		}
	}
	return run, gorm.ErrRecordNotFound
}

func buildC2MStandardReport(run DBC2MReportRun) (c2mStandardReport, error) {
	parameters, err := rawReportJSON(run.ParamsJSON, "{}")
	if err != nil {
		return c2mStandardReport{}, err
	}
	diagnostics, err := rawReportJSON(run.DiagnosticsJSON, "{}")
	if err != nil {
		return c2mStandardReport{}, err
	}
	alignment, err := rawReportJSON(run.TransformJSON, "null")
	if err != nil {
		return c2mStandardReport{}, err
	}
	comparison := rebarComparisonJSON(diagnostics)
	if len(comparison) == 0 || !json.Valid(comparison) {
		comparison = json.RawMessage("null")
	}
	inspection := rebarInspectionJSON(diagnostics)
	unavailable := ""
	if len(inspection) == 0 || !json.Valid(inspection) || string(inspection) == "null" {
		inspection = json.RawMessage("null")
		unavailable = "此历史结果未保存 rebar-inspection-v1 检验数据"
	}
	return c2mStandardReport{
		Schema:        "cloudbim-rebar-inspection-report-v1",
		Source:        c2mStandardReportSource{ModelScanFileID: run.ScanID, ModelBimFileID: run.BimID},
		ResultVersion: run.ResultVersion, CreatedAt: run.CreatedAt, AlgorithmVersion: run.AlgorithmVersion,
		Profile: run.Profile, MetricDirection: run.MetricDirection, InputFingerprint: run.InputFingerprint,
		AlignmentMatrix: alignment, Parameters: parameters, Inspection: inspection,
		InspectionUnavailableReason: unavailable, Comparison: comparison, Diagnostics: diagnostics,
	}, nil
}

func (a *app) downloadC2MReportJSON(c *gin.Context) {
	version := strings.TrimSpace(c.Param("version"))
	if version == "" || len(version) > 256 {
		fail(c, http.StatusBadRequest, "C2M 报告版本无效")
		return
	}
	run, err := a.findC2MReportRun(userID(c), version)
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			fail(c, http.StatusNotFound, "C2M 报告不存在")
		} else {
			fail(c, http.StatusInternalServerError, "查询 C2M 报告失败")
		}
		return
	}
	report, err := buildC2MStandardReport(run)
	if err != nil {
		fail(c, http.StatusInternalServerError, "保存的 C2M 报告格式无效")
		return
	}
	nameVersion := version
	if len(nameVersion) > 12 {
		nameVersion = nameVersion[:12]
	}
	for _, r := range nameVersion {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_') {
			nameVersion = "report"
			break
		}
	}
	c.Header("Content-Disposition", fmt.Sprintf(`attachment; filename="rebar-inspection-%s.json"`, nameVersion))
	c.Header("Cache-Control", "private, no-store")
	c.JSON(http.StatusOK, report)
}
