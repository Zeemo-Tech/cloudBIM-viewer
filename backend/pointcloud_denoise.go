package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type denoiseRequest struct {
	ModelScanFileID int64 `json:"modelScanFileId"`
	ModelBimFileID  int64 `json:"modelBimFileId"`
	NormalK         int   `json:"normalK"`
}

type denoiseManifest struct {
	AlgorithmVersion           string           `json:"algorithmVersion"`
	SourceSHA256               string           `json:"sourceSha256"`
	DesignFingerprint          string           `json:"designFingerprint"`
	InputFingerprint           string           `json:"inputFingerprint"`
	PointsBefore               int64            `json:"pointsBefore"`
	PointsAfter                int64            `json:"pointsAfter"`
	Counts                     map[string]int64 `json:"counts"`
	PreviewPointCount          int64            `json:"previewPointCount"`
	PreviewOrigin              []float64        `json:"previewOrigin"`
	NormalK                    int              `json:"normalK"`
	ElapsedSeconds             float64          `json:"elapsedSeconds"`
	InstanceCount              int              `json:"instanceCount"`
	InstanceContract           string           `json:"instanceContract"`
	InstancesSHA256            string           `json:"instancesSha256"`
	ControlNetSchema           string           `json:"controlNetSchema"`
	ControlNetSHA256           string           `json:"controlNetSha256"`
	ControlNetAlgorithmVersion string           `json:"controlNetAlgorithmVersion"`
}

const (
	productionDenoiseAlgorithmPrefix = "denoise-v4-control-net+"
	rebarControlNetEvidenceSchema    = "rebar-control-net-evidence-v1"
)

func denoiseKind(bimID int64) string { return fmt.Sprintf("pointcloud-denoise-%d", bimID) }

func (a *app) denoiseInputs(scan Asset, bimID, ownerID int64) (string, *RebarBimPrior, string, error) {
	source, err := a.resolveScanSourcePath(scan, ownerID)
	if err != nil {
		return "", nil, "", err
	}
	prior, err := a.resolveBimPrior(scan.ID, ownerID, &rebarBimSelection{BimAssetID: bimID}, false)
	if err != nil {
		return "", nil, "", err
	}
	if prior.IFCPath == "" {
		return "", nil, "", errors.New("设计辅助去噪需要原始 IFC 设计模型")
	}
	matrix, _ := json.Marshal(prior.ScanToBim)
	fingerprint, err := c2mInputFingerprint(string(matrix), source, backendDataPath(a.cfg.DataDir, prior.ModelPath, a.cfg.MeshServiceStorageDir), prior.Fingerprint)
	return source, prior, fingerprint, err
}

func (a *app) denoiseRow(scan Asset, bimID int64) (DBAssetDerivative, denoiseManifest, error) {
	var row DBAssetDerivative
	var manifest denoiseManifest
	err := a.db.Where("asset_id = ? AND kind = ?", scan.ID, denoiseKind(bimID)).First(&row).Error
	if err != nil {
		return row, manifest, err
	}
	if row.Status != "ready" || json.Unmarshal([]byte(row.MetadataJSON), &manifest) != nil || manifest.InputFingerprint == "" {
		return row, manifest, errors.New("去噪结果不可用，请重新运行")
	}
	return row, manifest, nil
}

func (a *app) denoiseFresh(scan Asset, bimID, ownerID int64, row DBAssetDerivative, manifest denoiseManifest) error {
	if !strings.HasPrefix(manifest.AlgorithmVersion, productionDenoiseAlgorithmPrefix) {
		return errors.New("此去噪结果不是当前控制网生产版本，请重新执行第二步分类与去噪")
	}
	if manifest.InstanceContract != "rebar-instance-map-v1" || !hex64(manifest.InstancesSHA256) {
		return errors.New("此去噪结果未保存钢筋实例与设计对应关系，请重新执行第二步分类与去噪")
	}
	source, _, current, err := a.denoiseInputs(scan, bimID, ownerID)
	if err != nil {
		return err
	}
	if current != manifest.InputFingerprint {
		return errors.New("点云、设计模型或配准已变化，请重新去噪")
	}
	if digest, err := fileContentHash(source); err != nil || digest != manifest.SourceSHA256 {
		return errors.New("去噪输入点云已变化，请重新去噪")
	}
	for _, name := range []string{"cleaned.las", "preview.ply", "instance-map.json", "control-net.json"} {
		if _, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, name)); err != nil {
			return errors.New("去噪文件缺失，请重新去噪")
		}
	}
	path, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, "instance-map.json"))
	if err != nil {
		return err
	}
	if digest, err := fileContentHash(path); err != nil || digest != manifest.InstancesSHA256 {
		return errors.New("钢筋实例对应文件已变化，请重新执行第二步分类与去噪")
	}
	if err := validateStoredControlNet(scan, row, manifest); err != nil {
		return err
	}
	return nil
}

func validateStoredControlNet(scan Asset, row DBAssetDerivative, manifest denoiseManifest) error {
	if manifest.ControlNetSchema != rebarControlNetEvidenceSchema || !hex64(manifest.ControlNetSHA256) || strings.TrimSpace(manifest.ControlNetAlgorithmVersion) == "" {
		return errors.New("去噪结果缺少有效的控制网来源信息，请重新执行第二步分类与去噪")
	}
	controlPath, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, "control-net.json"))
	if err != nil {
		return errors.New("控制网文件缺失，请重新执行第二步分类与去噪")
	}
	if digest, hashErr := fileContentHash(controlPath); hashErr != nil || digest != manifest.ControlNetSHA256 {
		return errors.New("控制网文件已变化，请重新执行第二步分类与去噪")
	}
	controlRaw, err := os.ReadFile(controlPath)
	if err != nil {
		return errors.New("控制网文件不可读，请重新执行第二步分类与去噪")
	}
	mapPath, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, "instance-map.json"))
	if err != nil {
		return errors.New("钢筋实例对应文件缺失，请重新执行第二步分类与去噪")
	}
	mapRaw, err := os.ReadFile(mapPath)
	if err != nil {
		return errors.New("钢筋实例对应文件不可读，请重新执行第二步分类与去噪")
	}
	var control any
	var instanceMap struct {
		ControlNet json.RawMessage `json:"controlNet"`
	}
	if json.Unmarshal(controlRaw, &control) != nil || json.Unmarshal(mapRaw, &instanceMap) != nil || len(instanceMap.ControlNet) == 0 {
		return errors.New("控制网证据格式无效，请重新执行第二步分类与去噪")
	}
	var embedded any
	if json.Unmarshal(instanceMap.ControlNet, &embedded) != nil || !reflect.DeepEqual(control, embedded) {
		return errors.New("控制网文件与实例映射不一致，请重新执行第二步分类与去噪")
	}
	envelope, ok := control.(map[string]any)
	if !ok || reportString(envelope["schema"]) != rebarControlNetEvidenceSchema || reportString(envelope["coordinateFrame"]) != "scan" || reportString(envelope["algorithmVersion"]) != manifest.ControlNetAlgorithmVersion {
		return errors.New("控制网证据来源无效，请重新执行第二步分类与去噪")
	}
	if _, ok := envelope["report"].(map[string]any); !ok {
		return errors.New("控制网证据缺少拟合报告，请重新执行第二步分类与去噪")
	}
	return nil
}

// Both C2M pipelines require a current result from the design-guided workflow.
func (a *app) resolveC2MScanPath(scan Asset, bimID, ownerID int64) (string, error) {
	row, manifest, err := a.denoiseRow(scan, bimID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return "", errors.New("请先完成点云分类与去噪")
	}
	if err != nil {
		return "", err
	}
	if err = a.denoiseFresh(scan, bimID, ownerID, row, manifest); err != nil {
		return "", err
	}
	return rebarFile(scan.Dir, filepath.Join(row.RelativePath, "cleaned.las"))
}

func (a *app) denoiseResponse(scan Asset, bimID, ownerID int64, row DBAssetDerivative, manifest denoiseManifest) gin.H {
	freshErr := a.denoiseFresh(scan, bimID, ownerID, row, manifest)
	data := gin.H{"result": manifest, "version": row.Version, "fresh": freshErr == nil, "updatedAt": row.UpdatedAt}
	if freshErr != nil {
		data["staleReason"] = freshErr.Error()
	}
	return data
}

func (a *app) getDenoiseLatest(c *gin.Context) {
	scanID, bimID, valid := c2mPairQuery(c)
	if !valid {
		fail(c, 400, "Scan 与 BIM 资产 ID 非法")
		return
	}
	scan, scanOK := a.getAssetByID(c, scanID, "pointcloud")
	_, bimOK := a.getAssetByID(c, bimID, "bim")
	if !scanOK || !bimOK {
		fail(c, 404, "点云或设计模型不存在")
		return
	}
	row, manifest, err := a.denoiseRow(scan, bimID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		ok(c, nil)
		return
	}
	if err != nil {
		fail(c, 409, err.Error())
		return
	}
	ok(c, a.denoiseResponse(scan, bimID, userID(c), row, manifest))
}

func (a *app) computeDenoise(c *gin.Context) {
	var req denoiseRequest
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID <= 0 || req.ModelBimFileID <= 0 {
		fail(c, 400, "Scan 与 BIM 资产 ID 非法")
		return
	}
	if req.NormalK == 0 {
		req.NormalK = 32
	}
	if req.NormalK != 32 {
		fail(c, 400, "去噪复用上传时计算的法向量，不支持重新指定近邻数")
		return
	}
	unlock := a.lockC2MOperation(userID(c), req.ModelScanFileID, req.ModelBimFileID)
	defer unlock()
	scan, found := a.getAssetByID(c, req.ModelScanFileID, "pointcloud")
	if !found {
		fail(c, 404, "点云不存在")
		return
	}
	source, prior, fingerprint, err := a.denoiseInputs(scan, req.ModelBimFileID, userID(c))
	if err != nil {
		fail(c, 409, err.Error())
		return
	}
	sourceDigest, err := fileContentHash(source)
	if err != nil {
		fail(c, 409, "点云输入文件不可读")
		return
	}
	version := randomID()
	relative := filepath.Join("denoise", fmt.Sprint(req.ModelBimFileID), version)
	output := filepath.Join(scan.Dir, relative)
	if err := os.MkdirAll(filepath.Dir(output), 0770); err != nil {
		fail(c, 500, "无法创建去噪输出目录")
		return
	}
	payload := gin.H{"sourcePath": meshServicePath(a.cfg.DataDir, source, a.cfg.MeshServiceStorageDir), "ifcPath": prior.IFCPath, "modelPath": prior.ModelPath, "transform": prior.ScanToBim, "outputPath": meshServicePath(a.cfg.DataDir, output, a.cfg.MeshServiceStorageDir), "normalK": req.NormalK}
	body, _ := json.Marshal(payload)
	request, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, strings.TrimRight(a.cfg.MeshServiceURL, "/")+"/pointcloud-denoise/compute", bytes.NewReader(body))
	if err != nil {
		fail(c, 500, "构建去噪请求失败")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 60 * time.Minute}).Do(request)
	if err != nil {
		fail(c, 502, "调用去噪服务失败，请检查服务状态后重试")
		return
	}
	defer response.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if response.StatusCode != 200 {
		status := http.StatusBadGateway
		if response.StatusCode == 429 {
			status = 429
			copyRetryAfter(c.Writer.Header(), response)
		}
		if response.StatusCode == 400 || response.StatusCode == 422 {
			status = 400
		}
		fail(c, status, "点云去噪失败："+meshServiceError(raw))
		return
	}
	published := false
	defer func() {
		if !published {
			_ = os.RemoveAll(output)
		}
	}()
	var result denoiseManifest
	if json.Unmarshal(raw, &result) != nil || !validDenoiseManifest(result) {
		fail(c, 502, "去噪服务返回了无效结果")
		return
	}
	if result.SourceSHA256 != sourceDigest {
		fail(c, 502, "去噪服务返回的点云来源哈希不一致")
		return
	}
	for _, name := range []string{"cleaned.las", "preview.ply", "instance-map.json", "control-net.json"} {
		if _, err := rebarFile(scan.Dir, filepath.Join(relative, name)); err != nil {
			fail(c, 502, "去噪产物不存在或路径非法")
			return
		}
	}
	mapPath, err := rebarFile(scan.Dir, filepath.Join(relative, "instance-map.json"))
	if err != nil {
		fail(c, 502, "钢筋实例映射文件不可用")
		return
	}
	if digest, err := fileContentHash(mapPath); err != nil || digest != result.InstancesSHA256 {
		fail(c, 502, "钢筋实例映射哈希与去噪结果不一致")
		return
	}
	controlPath, err := rebarFile(scan.Dir, filepath.Join(relative, "control-net.json"))
	if err != nil {
		fail(c, 502, "控制网文件不可用")
		return
	}
	if digest, err := fileContentHash(controlPath); err != nil || digest != result.ControlNetSHA256 {
		fail(c, 502, "控制网文件哈希与去噪结果不一致")
		return
	}
	if err := validateStoredControlNet(scan, DBAssetDerivative{RelativePath: relative}, result); err != nil {
		fail(c, 502, err.Error())
		return
	}
	a.c2mMutationMu.Lock()
	defer a.c2mMutationMu.Unlock()
	_, _, current, err := a.denoiseInputs(scan, req.ModelBimFileID, userID(c))
	if err != nil || current != fingerprint {
		fail(c, 409, "计算期间配准或输入发生变化，请重新去噪")
		return
	}
	if currentDigest, err := fileContentHash(source); err != nil || currentDigest != sourceDigest {
		fail(c, 409, "计算期间点云输入发生变化，请重新去噪")
		return
	}
	result.InputFingerprint = fingerprint
	metadata, _ := json.Marshal(result)
	row := DBAssetDerivative{AssetID: scan.ID, Kind: denoiseKind(req.ModelBimFileID), Format: "las", Status: "ready", RelativePath: relative, EntryPath: "cleaned.las", Version: version, MetadataJSON: string(metadata)}
	err = a.db.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "asset_id"}, {Name: "kind"}}, DoUpdates: clause.AssignmentColumns([]string{"status", "relative_path", "entry_path", "version", "metadata_json", "updated_at"})}).Create(&row).Error
	if err != nil {
		fail(c, 500, "保存去噪结果失败")
		return
	}
	published = true
	// Old version directories remain immutable for in-flight analysis readers.
	ok(c, a.denoiseResponse(scan, req.ModelBimFileID, userID(c), row, result))
}

func validDenoiseManifest(m denoiseManifest) bool {
	if !strings.HasPrefix(m.AlgorithmVersion, productionDenoiseAlgorithmPrefix) || m.InstanceContract != "rebar-instance-map-v1" || !hex64(m.InstancesSHA256) ||
		m.ControlNetSchema != rebarControlNetEvidenceSchema || !hex64(m.ControlNetSHA256) || strings.TrimSpace(m.ControlNetAlgorithmVersion) == "" {
		return false
	}
	if !hex64(m.SourceSHA256) || !hex64(m.DesignFingerprint) || m.PointsBefore < 3 || m.PointsAfter <= 0 || m.PointsAfter > m.PointsBefore || len(m.PreviewOrigin) != 3 || m.PreviewPointCount <= 0 || m.PreviewPointCount > 500000 || m.PreviewPointCount > m.PointsBefore {
		return false
	}
	var total int64
	for _, k := range []string{"unknown", "table", "fixture", "steel", "noise"} {
		v, ok := m.Counts[k]
		if !ok || v < 0 {
			return false
		}
		total += v
	}
	return total == m.PointsBefore && m.Counts["steel"] == m.PointsAfter
}

func (a *app) denoiseArtifact(c *gin.Context) {
	scanID, bimID, valid := c2mPairQuery(c)
	if !valid {
		fail(c, 400, "Scan 与 BIM 资产 ID 非法")
		return
	}
	scan, found := a.getAssetByID(c, scanID, "pointcloud")
	if !found {
		fail(c, 404, "点云不存在")
		return
	}
	row, manifest, err := a.denoiseRow(scan, bimID)
	if err != nil {
		fail(c, 404, "暂无去噪结果")
		return
	}
	if c.Query("version") != row.Version {
		fail(c, 409, "去噪版本已变化，请刷新后重试")
		return
	}
	if err = a.denoiseFresh(scan, bimID, userID(c), row, manifest); err != nil {
		fail(c, 409, err.Error())
		return
	}
	name := c.Param("name")
	if name != "cleaned.las" && name != "preview.ply" && name != "instance-map.json" && name != "control-net.json" {
		fail(c, 404, "文件不存在")
		return
	}
	path, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, name))
	if err != nil {
		fail(c, 404, "去噪文件不存在")
		return
	}
	c.Header("Cache-Control", "private, no-store")
	c.FileAttachment(path, name)
}
