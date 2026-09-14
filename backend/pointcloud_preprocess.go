package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const tableFreeKind = "table-free"

type pointcloudPreprocessManifest struct {
	AlgorithmVersion string          `json:"algorithmVersion"`
	SourceSHA256     string          `json:"sourceSha256"`
	InputFingerprint string          `json:"inputFingerprint,omitempty"`
	NormalK          int             `json:"normalK"`
	PointsBefore     int64           `json:"pointsBefore"`
	PointsAfter      int64           `json:"pointsAfter"`
	TablePoints      int64           `json:"tablePoints"`
	Detected         bool            `json:"detected"`
	Plane            json.RawMessage `json:"plane,omitempty"`
}

func validPointcloudPreprocessManifest(m pointcloudPreprocessManifest) bool {
	digest, err := hex.DecodeString(m.SourceSHA256)
	return err == nil && len(digest) == 32 && m.AlgorithmVersion != "" && m.NormalK == 32 &&
		m.PointsBefore >= 3 && m.PointsAfter >= 3 && m.PointsAfter <= m.PointsBefore &&
		m.TablePoints == m.PointsBefore-m.PointsAfter && (m.Detected || m.TablePoints == 0)
}

func (a *app) pointcloudPreprocessRow(scan Asset) (DBAssetDerivative, pointcloudPreprocessManifest, error) {
	var row DBAssetDerivative
	var result pointcloudPreprocessManifest
	if err := a.db.Where("asset_id = ? AND kind = ?", scan.ID, tableFreeKind).First(&row).Error; err != nil {
		return row, result, err
	}
	if row.Status != "ready" || row.Version == "" || json.Unmarshal([]byte(row.MetadataJSON), &result) != nil || !validPointcloudPreprocessManifest(result) {
		return row, result, errors.New("台面处理结果无效，请在点云预览中重新处理")
	}
	source, err := a.resolveRawScanSourcePath(scan, scan.OwnerID)
	if err != nil {
		return row, result, err
	}
	current, err := c2mInputFingerprint("preprocess", source, source)
	if err != nil || current != result.InputFingerprint {
		return row, result, errors.New("原始点云已变化，请在点云预览中重新处理")
	}
	digest, err := fileContentHash(source)
	if err != nil || digest != result.SourceSHA256 {
		return row, result, errors.New("原始点云内容已变化，请在点云预览中重新处理")
	}
	for _, name := range []string{"cleaned.las", "annotated.las", "manifest.json", "tiles/tileset.json"} {
		path, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, name))
		if err != nil {
			return row, result, errors.New("台面处理文件缺失，请在点云预览中重新处理")
		}
		if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
			return row, result, errors.New("台面处理文件无效，请在点云预览中重新处理")
		}
	}
	return row, result, nil
}

// Every analysis consumer uses the retained scan; archival reads use resolveRawScanSourcePath.
// A missing/stale derivative must never silently switch an analysis back to the raw scan.
func (a *app) resolveScanSourcePath(scan Asset, ownerID int64) (string, error) {
	if ownerID <= 0 || scan.OwnerID != ownerID {
		return "", errors.New("点云不存在")
	}
	row, _, err := a.pointcloudPreprocessRow(scan)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return "", errors.New("请先在点云预览中完成法向量与台面处理")
	}
	if err != nil {
		return "", err
	}
	return rebarFile(scan.Dir, filepath.Join(row.RelativePath, "cleaned.las"))
}

// Callers hold the scan lock, shared with deletion. Publish only after LAS and tiles succeed.
func (a *app) buildPointcloudPreprocess(ctx context.Context, scan Asset) (DBAssetDerivative, pointcloudPreprocessManifest, error) {
	if row, result, err := a.pointcloudPreprocessRow(scan); err == nil {
		return row, result, nil
	}
	var empty DBAssetDerivative
	var result pointcloudPreprocessManifest
	ctx, cancel := context.WithTimeout(ctx, 60*time.Minute)
	defer cancel()
	if a.meshProviderGate != nil {
		release, err := a.acquireMeshProvider(ctx)
		if err != nil {
			return empty, result, err
		}
		defer release()
	}
	source, err := a.resolveRawScanSourcePath(scan, scan.OwnerID)
	if err != nil {
		return empty, result, err
	}
	version := randomID()
	relative := filepath.Join("preprocess", version)
	output := filepath.Join(scan.Dir, relative)
	if err = os.MkdirAll(filepath.Dir(output), 0770); err != nil {
		return empty, result, err
	}
	// Check both metadata and content: an equal-size replacement can preserve mtime.
	before, err := c2mInputFingerprint("preprocess", source, source)
	if err != nil {
		return empty, result, err
	}
	body, _ := json.Marshal(gin.H{"sourcePath": meshServicePath(a.cfg.DataDir, source, a.cfg.MeshServiceStorageDir), "outputPath": meshServicePath(a.cfg.DataDir, output, a.cfg.MeshServiceStorageDir)})
	var response *http.Response
	for {
		request, requestErr := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(a.cfg.MeshServiceURL, "/")+"/pointcloud-preprocess/compute", bytes.NewReader(body))
		if requestErr != nil {
			return empty, result, requestErr
		}
		request.Header.Set("Content-Type", "application/json")
		response, err = (&http.Client{Timeout: 60 * time.Minute}).Do(request)
		if err != nil {
			return empty, result, fmt.Errorf("调用点云前处理服务失败: %w", err)
		}
		if response.StatusCode != http.StatusTooManyRequests {
			break
		}
		// Another heavy task may have entered through a legacy synchronous endpoint.
		// A queued upload must wait for capacity rather than becoming a failed asset.
		response.Body.Close()
		timer := time.NewTimer(5 * time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return empty, result, ctx.Err()
		case <-timer.C:
		}
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return empty, result, err
	}
	if response.StatusCode != http.StatusOK {
		return empty, result, fmt.Errorf("点云前处理失败: %s", meshServiceError(raw))
	}
	published := false
	defer func() {
		if !published {
			_ = os.RemoveAll(output)
		}
	}()
	if json.Unmarshal(raw, &result) != nil || !validPointcloudPreprocessManifest(result) {
		return empty, result, errors.New("点云前处理服务返回无效结果")
	}
	for _, name := range []string{"annotated.las", "cleaned.las", "manifest.json"} {
		if _, err = rebarFile(scan.Dir, filepath.Join(relative, name)); err != nil {
			return empty, result, fmt.Errorf("前处理产物缺失: %s", name)
		}
	}
	if err = buildPointCloud(ctx, filepath.Join(output, "cleaned.las"), output, a.cfg.PointcloudSubsample); err != nil {
		return empty, result, err
	}
	if tiles, err := readTileset(filepath.Join(output, "tiles", "tileset.json")); err != nil || !hasTileContent(output, tiles) {
		return empty, result, errors.New("移除台面后的预览瓦片无效")
	}
	after, err := c2mInputFingerprint("preprocess", source, source)
	if err != nil || before != after {
		return empty, result, errors.New("前处理期间点云发生变化，请重新处理")
	}
	digest, err := fileContentHash(source)
	if err != nil || digest != result.SourceSHA256 {
		return empty, result, errors.New("前处理期间点云内容发生变化，请重新处理")
	}
	result.InputFingerprint = before
	metadata, _ := json.Marshal(result)
	row := DBAssetDerivative{AssetID: scan.ID, Kind: tableFreeKind, Format: "3d-tiles", Status: "ready", RelativePath: relative, EntryPath: "tiles/tileset.json", Version: version, MetadataJSON: string(metadata)}
	err = a.db.Transaction(func(tx *gorm.DB) error {
		var asset DBAsset
		if err := tx.Where("id = ? AND owner_id = ?", scan.ID, scan.OwnerID).First(&asset).Error; err != nil {
			return err
		}
		return tx.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "asset_id"}, {Name: "kind"}}, DoUpdates: clause.AssignmentColumns([]string{"status", "relative_path", "entry_path", "version", "metadata_json", "updated_at"})}).Create(&row).Error
	})
	if err != nil {
		return empty, result, err
	}
	published = true
	return row, result, nil
}

func (a *app) getPointcloudPreprocess(c *gin.Context) {
	scan, found := a.getAsset(c)
	if !found || scan.Type != "pointcloud" {
		fail(c, 404, "点云不存在")
		return
	}
	row, result, err := a.pointcloudPreprocessRow(*scan)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		ok(c, (*pointcloudPreprocessManifest)(nil))
		return
	}
	if err != nil {
		fail(c, 409, err.Error())
		return
	}
	ok(c, gin.H{"result": result, "version": row.Version})
}

func (a *app) computePointcloudPreprocess(c *gin.Context) {
	scan, found := a.getAsset(c)
	if !found || scan.Type != "pointcloud" || scan.Status != "ready" {
		fail(c, 404, "点云不存在或尚未就绪")
		return
	}
	lock := a.rebarLock(scan.ID)
	if !lock.TryLock() {
		fail(c, 409, "点云正在处理中，请稍后重试")
		return
	}
	defer lock.Unlock()
	row, result, err := a.buildPointcloudPreprocess(c.Request.Context(), *scan)
	if err != nil {
		fail(c, 502, err.Error())
		return
	}
	ok(c, gin.H{"result": result, "version": row.Version})
}
