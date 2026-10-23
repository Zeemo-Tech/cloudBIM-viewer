package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

const rebarFineAlignmentMethod = "rebar-weighted-rigid-v1"
const icpFineAlignmentMethod = "point-to-plane-icp"

type fineAlignmentRequest struct {
	ModelScanFileID           int64   `json:"modelScanFileId"`
	ModelBimFileID            int64   `json:"modelBimFileId"`
	Method                    string  `json:"method"`
	MaxCorrespondenceDistance float64 `json:"maxCorrespondenceDistance"`
	RMSERegressRatio          float64 `json:"rmseRegressRatio"`
	FitnessRegressRatio       float64 `json:"fitnessRegressRatio"`
	ApplyWhenRegressed        bool    `json:"applyWhenRegressed"`
}

// Legacy metric names remain stable. The rebar solver measures section-center
// distances to design axes; its displacement is measured at the scan pivot.
type fineAlignmentMetrics struct {
	InitFitness       float64 `json:"initFitness"`
	InitRMSE          float64 `json:"initRmse"`
	FineFitness       float64 `json:"fineFitness"`
	FineRMSE          float64 `json:"fineRmse"`
	DeltaTranslationM float64 `json:"deltaTranslationM"`
	DeltaRotationDeg  float64 `json:"deltaRotationDeg"`
	ElapsedS          float64 `json:"elapsedS"`
	SourceTotalPoints int     `json:"sourceTotalPoints"`
	TargetPoints      int     `json:"targetPoints"`
}

type fineAlignmentResponse struct {
	ModelScanFileID      int64           `json:"modelScanFileId"`
	ModelBimFileID       int64           `json:"modelBimFileId"`
	ModelBIMBuildingName *string         `json:"modelBimBuildingName,omitempty"`
	ModelMatrix          []float64       `json:"modelMatrix"`
	Method               string          `json:"method"`
	Diagnostics          map[string]any  `json:"diagnostics"`
	AuditVersion         string          `json:"auditVersion,omitempty"`
	Fallback             bool            `json:"fallback"`
	Regressed            bool            `json:"regressed"`
	AppliedFineResult    bool            `json:"appliedFineResult"`
	Metrics              json.RawMessage `json:"metrics"`
	RMSERegressRatio     float64         `json:"rmseRegressRatio"`
	FitnessRegressRatio  float64         `json:"fitnessRegressRatio"`
	ApplyWhenRegressed   bool            `json:"applyWhenRegressed"`
	ModelRotationQx      float64         `json:"modelRotationQx"`
	ModelRotationQy      float64         `json:"modelRotationQy"`
	ModelRotationQz      float64         `json:"modelRotationQz"`
	ModelRotationQw      float64         `json:"modelRotationQw"`
	ModelTranslationX    float64         `json:"modelTranslationX"`
	ModelTranslationY    float64         `json:"modelTranslationY"`
	ModelTranslationZ    float64         `json:"modelTranslationZ"`
}

type fineAlignmentServiceResponse struct {
	Transform  []float64 `json:"transform"`
	Quaternion struct {
		Qx float64 `json:"qx"`
		Qy float64 `json:"qy"`
		Qz float64 `json:"qz"`
		Qw float64 `json:"qw"`
	} `json:"quaternion"`
	Translation struct {
		Tx float64 `json:"tx"`
		Ty float64 `json:"ty"`
		Tz float64 `json:"tz"`
	} `json:"translation"`
	Method      string         `json:"method"`
	Diagnostics map[string]any `json:"diagnostics"`
	Fallback    bool           `json:"fallback"`
	Regressed   bool           `json:"regressed"`
	Applied     bool           `json:"appliedFineResult"`
	// Forward the complete metric object, including future additive fields.
	Metrics json.RawMessage `json:"metrics"`
}

type fineAlignmentSnapshot struct {
	Alignment DBAlignment
	Initial   []float64
	Denoise   DBAssetDerivative
	Manifest  denoiseManifest
	Method    string
}

func validFineRigidMatrix(m []float64) bool {
	if len(m) != 16 {
		return false
	}
	for _, value := range m {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return false
		}
	}
	if math.Abs(m[3])+math.Abs(m[7])+math.Abs(m[11])+math.Abs(m[15]-1) > 1e-8 {
		return false
	}
	for col := 0; col < 3; col++ {
		for other := 0; other < 3; other++ {
			dot := 0.0
			for row := 0; row < 3; row++ {
				dot += m[col*4+row] * m[other*4+row]
			}
			want := 0.0
			if col == other {
				want = 1
			}
			if math.Abs(dot-want) > 1e-5 {
				return false
			}
		}
	}
	det := m[0]*(m[5]*m[10]-m[9]*m[6]) - m[4]*(m[1]*m[10]-m[9]*m[2]) + m[8]*(m[1]*m[6]-m[5]*m[2])
	return math.Abs(det-1) <= 1e-5
}

func (a *app) snapshotFineAlignment(scan, bim Asset, ownerID int64, method string) (fineAlignmentSnapshot, int, error) {
	a.c2mMutationMu.Lock()
	defer a.c2mMutationMu.Unlock()
	var snapshot fineAlignmentSnapshot
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, ownerID).First(&snapshot.Alignment).Error; err != nil {
		return snapshot, 404, errors.New("请先保存粗配准矩阵")
	}
	if json.Unmarshal([]byte(snapshot.Alignment.MatrixJSON), &snapshot.Initial) != nil || !validFineRigidMatrix(snapshot.Initial) {
		return snapshot, 409, errors.New("粗配准矩阵不是有效的刚体变换，请重新保存")
	}
	row, manifest, err := a.denoiseRow(scan, bim.ID)
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) && row.ID == 0 {
		return snapshot, 500, errors.New("查询分类与去噪结果失败")
	}
	snapshot.Denoise, snapshot.Manifest = row, manifest
	if method == "icp" || (method == "auto" && errors.Is(err, gorm.ErrRecordNotFound)) {
		snapshot.Method = "icp"
		return snapshot, 0, nil
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return snapshot, 409, errors.New("钢筋精配准需要先完成第二步分类与去噪")
	}
	if err != nil {
		return snapshot, 409, err
	}
	if !validDenoiseManifest(manifest) || manifest.InstanceCount <= 0 || manifest.Counts["steel"] <= 0 {
		return snapshot, 409, errors.New("钢筋分类结果无效，请重新完成第二步分类与去噪")
	}
	if err := a.denoiseFresh(scan, bim.ID, ownerID, row, manifest); err != nil {
		return snapshot, 409, err
	}
	snapshot.Method = "rebar"
	return snapshot, 0, nil
}

func (a *app) fineInputPath(path string) (string, error) {
	rel, err := filepath.Rel(a.cfg.DataDir, path)
	if err != nil {
		return "", err
	}
	return rebarFile(a.cfg.DataDir, rel)
}

func validateFineAlignmentResult(result fineAlignmentServiceResponse, snapshot fineAlignmentSnapshot, allowRegressed bool) (fineAlignmentMetrics, error) {
	var metrics fineAlignmentMetrics
	invalid := errors.New("精细配准服务返回了无效或不一致的刚体结果")
	if !validFineRigidMatrix(result.Transform) || result.Fallback != (result.Regressed && !result.Applied) || (result.Regressed && result.Applied && !allowRegressed) {
		return metrics, invalid
	}
	if !result.Applied && !sameJSON(result.Transform, snapshot.Initial) {
		return metrics, invalid
	}
	if (snapshot.Method == "rebar" && result.Method != rebarFineAlignmentMethod) || (snapshot.Method == "icp" && result.Method != "" && result.Method != icpFineAlignmentMethod) {
		return metrics, invalid
	}
	q := result.Quaternion
	norm := q.Qx*q.Qx + q.Qy*q.Qy + q.Qz*q.Qz + q.Qw*q.Qw
	if math.Abs(norm-1) > 1e-5 || math.IsNaN(norm) || math.IsInf(norm, 0) {
		return metrics, invalid
	}
	rotation := rotationFromQ([4]float64{q.Qw, q.Qx, q.Qy, q.Qz})
	for col := 0; col < 3; col++ {
		for row := 0; row < 3; row++ {
			if math.Abs(result.Transform[col*4+row]-rotation[row][col]) > 1e-5 {
				return metrics, invalid
			}
		}
	}
	for i, value := range []float64{result.Translation.Tx, result.Translation.Ty, result.Translation.Tz} {
		if math.IsNaN(value) || math.IsInf(value, 0) || math.Abs(value-result.Transform[12+i]) > 1e-8 {
			return metrics, invalid
		}
	}
	var values map[string]json.RawMessage
	if json.Unmarshal(result.Metrics, &values) != nil || json.Unmarshal(result.Metrics, &metrics) != nil {
		return metrics, invalid
	}
	for _, name := range []string{"initFitness", "initRmse", "fineFitness", "fineRmse", "deltaTranslationM", "deltaRotationDeg", "elapsedS", "sourceTotalPoints", "targetPoints"} {
		var value float64
		if raw, ok := values[name]; !ok || string(raw) == "null" || json.Unmarshal(raw, &value) != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
			return metrics, invalid
		}
	}
	if metrics.InitFitness > 1 || metrics.FineFitness > 1 || metrics.SourceTotalPoints <= 0 || metrics.TargetPoints <= 0 || (snapshot.Method == "rebar" && result.Diagnostics == nil) {
		return metrics, invalid
	}
	return metrics, nil
}

// The immutable audit records the complete previous database row and candidate.
// Its existence records the attempted application; it is not a commit marker.
func (a *app) archiveFineAlignment(scan Asset, req fineAlignmentRequest, snapshot fineAlignmentSnapshot, candidate fineAlignmentServiceResponse) (string, error) {
	scanDir, err := a.fineInputPath(scan.Dir)
	if err != nil {
		return "", err
	}
	historyDir := filepath.Join(scanDir, "alignment-history")
	if err := os.Mkdir(historyDir, 0770); err != nil && !errors.Is(err, os.ErrExist) {
		return "", err
	}
	historyDir, err = rebarFile(scanDir, "alignment-history")
	if err != nil {
		return "", err
	}
	version := time.Now().UTC().Format("20060102T150405.000000000Z") + "-" + randomID()
	raw, err := json.MarshalIndent(gin.H{"schema": "fine-alignment-audit-v1", "version": version, "createdAt": time.Now().UTC(), "previousAlignment": snapshot.Alignment, "denoiseSnapshot": snapshot.Denoise, "request": req, "candidate": candidate}, "", "  ")
	if err != nil {
		return "", err
	}
	file, err := os.OpenFile(filepath.Join(historyDir, version+".json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0440)
	if err != nil {
		return "", err
	}
	_, writeErr := file.Write(raw)
	syncErr := file.Sync()
	closeErr := file.Close()
	if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
		return "", err
	}
	return version, nil
}

func (a *app) applyFineAlignment(scan, bim Asset, ownerID int64, req fineAlignmentRequest, snapshot fineAlignmentSnapshot, candidate fineAlignmentServiceResponse, metrics fineAlignmentMetrics) (string, int, error) {
	a.c2mMutationMu.Lock()
	defer a.c2mMutationMu.Unlock()
	var current DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, ownerID).First(&current).Error; err != nil || !sameJSON(current, snapshot.Alignment) {
		return "", 409, errors.New("计算期间配准结果已变化，本次精调未保存，请刷新后重试")
	}
	var denoise DBAssetDerivative
	err := a.db.Where("asset_id = ? AND kind = ?", scan.ID, denoiseKind(bim.ID)).First(&denoise).Error
	if (snapshot.Denoise.ID == 0 && !errors.Is(err, gorm.ErrRecordNotFound)) || (snapshot.Denoise.ID != 0 && (err != nil || !sameJSON(denoise, snapshot.Denoise))) {
		return "", 409, errors.New("计算期间分类与去噪版本已变化，本次精调未保存，请刷新后重试")
	}
	if snapshot.Method == "rebar" {
		if err := a.denoiseFresh(scan, bim.ID, ownerID, denoise, snapshot.Manifest); err != nil {
			return "", 409, err
		}
	}
	auditVersion, err := a.archiveFineAlignment(scan, req, snapshot, candidate)
	if err != nil {
		return "", 500, fmt.Errorf("保存原配准审计快照失败: %w", err)
	}
	matrixJSON, _ := json.Marshal(candidate.Transform)
	update := a.db.Model(&DBAlignment{}).Where("id = ? AND owner_id = ? AND matrix_json = ?", current.ID, ownerID, snapshot.Alignment.MatrixJSON).Updates(map[string]any{
		"qx": candidate.Quaternion.Qx, "qy": candidate.Quaternion.Qy, "qz": candidate.Quaternion.Qz, "qw": candidate.Quaternion.Qw,
		"tx": candidate.Translation.Tx, "ty": candidate.Translation.Ty, "tz": candidate.Translation.Tz, "matrix_json": string(matrixJSON), "rmse": metrics.FineRMSE,
	})
	if update.Error != nil {
		return "", 500, errors.New("保存精细配准结果失败")
	}
	if update.RowsAffected != 1 {
		return "", 409, errors.New("配准结果已变化，本次精调未保存，请刷新后重试")
	}
	return auditVersion, 0, nil
}

func (a *app) fineAlignment(c *gin.Context) {
	var req fineAlignmentRequest
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID <= 0 || req.ModelBimFileID <= 0 {
		fail(c, 400, "文件 ID 非法")
		return
	}
	if req.Method == "" {
		req.Method = "auto"
	}
	if req.Method != "auto" && req.Method != "rebar" && req.Method != "icp" {
		fail(c, 400, "精配准 method 必须为 auto、rebar 或 icp")
		return
	}
	if a.cfg.MeshServiceURL == "" {
		fail(c, 503, "未配置精细化配准服务，请设置 MESH_SERVICE_URL")
		return
	}
	scan, scanOK := a.getAssetByID(c, req.ModelScanFileID, "pointcloud")
	bim, bimOK := a.getAssetByID(c, req.ModelBimFileID, "bim")
	if !scanOK || !bimOK {
		fail(c, 404, "点云或 BIM 资产不存在或尚未就绪")
		return
	}
	snapshot, status, err := a.snapshotFineAlignment(scan, bim, userID(c), req.Method)
	if err != nil {
		fail(c, status, err.Error())
		return
	}
	if req.MaxCorrespondenceDistance <= 0 {
		req.MaxCorrespondenceDistance = 0.3
	}
	if req.RMSERegressRatio <= 0 {
		req.RMSERegressRatio = 1.05
	}
	if req.FitnessRegressRatio <= 0 {
		req.FitnessRegressRatio = 0.95
	}
	payload := gin.H{"method": snapshot.Method, "init_transform": snapshot.Initial, "max_correspondence_distance": req.MaxCorrespondenceDistance, "rmse_regress_ratio": req.RMSERegressRatio, "fitness_regress_ratio": req.FitnessRegressRatio, "apply_when_regressed": req.ApplyWhenRegressed}
	if snapshot.Method == "rebar" {
		dir, pathErr := a.fineInputPath(filepath.Join(scan.Dir, snapshot.Denoise.RelativePath))
		if pathErr != nil {
			fail(c, 409, "分类与去噪产物路径无效")
			return
		}
		payload["denoise_dir"] = meshServicePath(a.cfg.DataDir, dir, a.cfg.MeshServiceStorageDir)
	} else {
		a.refreshRemeshState(&bim)
		if bim.RemeshStatus != "succeeded" {
			fail(c, 409, "BIM 尚未生成精调所需的有效均匀化网格，请重新执行")
			return
		}
		source, pathErr := a.resolveScanSourcePath(scan, userID(c))
		if pathErr == nil {
			source, pathErr = a.fineInputPath(source)
		}
		mesh, meshErr := a.fineInputPath(filepath.Join(bim.Dir, "mesh_remesh.ply"))
		if pathErr != nil || meshErr != nil {
			fail(c, 409, "点云或均匀化网格文件不存在或路径无效")
			return
		}
		payload["scan_path"], payload["mesh_path"] = meshServiceInputPaths(a.cfg.DataDir, a.cfg.MeshServiceStorageDir, source, mesh)
	}
	body, _ := json.Marshal(payload)
	request, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, strings.TrimRight(a.cfg.MeshServiceURL, "/")+"/align/fine", bytes.NewReader(body))
	if err != nil {
		fail(c, 500, "构建精调请求失败")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 30 * time.Minute}).Do(request)
	if err != nil {
		fail(c, 502, fmt.Sprintf("调用精细化配准服务失败: %v", err))
		return
	}
	defer resp.Body.Close()
	responseBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if readErr != nil {
		fail(c, 502, "读取精细化配准结果失败")
		return
	}
	if resp.StatusCode != http.StatusOK {
		status := http.StatusBadGateway
		if resp.StatusCode == 429 {
			status = 429
			copyRetryAfter(c.Writer.Header(), resp)
		} else if resp.StatusCode == 400 || resp.StatusCode == 422 {
			status = 400
		}
		fail(c, status, "精细化配准失败: "+meshServiceError(responseBody))
		return
	}
	var serviceResult fineAlignmentServiceResponse
	if json.Unmarshal(responseBody, &serviceResult) != nil {
		fail(c, 502, "解析精细化配准结果失败")
		return
	}
	metrics, err := validateFineAlignmentResult(serviceResult, snapshot, req.ApplyWhenRegressed)
	if err != nil {
		fail(c, 502, err.Error())
		return
	}
	if serviceResult.Method == "" {
		serviceResult.Method = icpFineAlignmentMethod
	}
	if serviceResult.Diagnostics == nil {
		serviceResult.Diagnostics = map[string]any{}
	}
	result := fineAlignmentResponse{
		ModelScanFileID: scan.ID, ModelBimFileID: bim.ID, ModelBIMBuildingName: stringPtr(bim.SourceName),
		ModelMatrix: serviceResult.Transform, Method: serviceResult.Method, Diagnostics: serviceResult.Diagnostics,
		Fallback: serviceResult.Fallback, Regressed: serviceResult.Regressed, AppliedFineResult: serviceResult.Applied, Metrics: serviceResult.Metrics,
		ModelRotationQx: serviceResult.Quaternion.Qx, ModelRotationQy: serviceResult.Quaternion.Qy, ModelRotationQz: serviceResult.Quaternion.Qz, ModelRotationQw: serviceResult.Quaternion.Qw,
		ModelTranslationX: serviceResult.Translation.Tx, ModelTranslationY: serviceResult.Translation.Ty, ModelTranslationZ: serviceResult.Translation.Tz,
		RMSERegressRatio: req.RMSERegressRatio, FitnessRegressRatio: req.FitnessRegressRatio, ApplyWhenRegressed: req.ApplyWhenRegressed,
	}
	if serviceResult.Applied {
		result.AuditVersion, status, err = a.applyFineAlignment(scan, bim, userID(c), req, snapshot, serviceResult, metrics)
		if err != nil {
			fail(c, status, err.Error())
			return
		}
	}
	ok(c, result)
}
