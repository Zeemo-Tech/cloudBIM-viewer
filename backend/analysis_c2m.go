package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

const analysisC2MKind = "analysis-c2m"

type AnalysisC2MFile struct {
	SHA256     string `json:"sha256"`
	ByteLength int64  `json:"byteLength"`
}
type AnalysisC2MTile struct {
	TileID       string           `json:"tileId"`
	IFCGlobalID  string           `json:"ifcGlobalId"`
	PartID       string           `json:"partId"`
	PositionHash string           `json:"positionHash"`
	VertexCount  int              `json:"vertexCount"`
	DistancePath string           `json:"distancePath"`
	SHA256       string           `json:"sha256"`
	ByteLength   int64            `json:"byteLength"`
	Stats        AnalysisC2MStats `json:"stats"`
}
type AnalysisC2MStats struct {
	KnownCount   int      `json:"knownCount"`
	UnknownCount int      `json:"unknownCount"`
	Min          *float64 `json:"min,omitempty"`
	Max          *float64 `json:"max,omitempty"`
	Mean         *float64 `json:"mean,omitempty"`
	Std          *float64 `json:"std,omitempty"`
}
type AnalysisC2MManifest struct {
	Schema          string `json:"schema"`
	Immutable       bool   `json:"immutable"`
	ContentHash     string `json:"contentHash"`
	UnknownEncoding struct {
		Type      string `json:"type"`
		Value     string `json:"value"`
		ByteOrder string `json:"byteOrder"`
	} `json:"unknownEncoding"`
	InputAnalysisMesh struct {
		ContentHash     string             `json:"contentHash"`
		ArtifactVersion string             `json:"artifactVersion"`
		ModelFrame      AnalysisModelFrame `json:"modelFrame"`
	} `json:"inputAnalysisMesh"`
	Algorithm struct {
		ID                    string         `json:"id"`
		ImplementationVersion string         `json:"implementationVersion"`
		ContractVersion       string         `json:"contractVersion"`
		EffectiveParameters   map[string]any `json:"effectiveParameters"`
	} `json:"algorithm"`
	Scan struct {
		ContentHash  string `json:"contentHash"`
		PointsBefore int    `json:"pointsBefore"`
		PointsAfter  int    `json:"pointsAfter"`
	} `json:"scan"`
	Transform  []float64         `json:"transformColumnMajor"`
	Tiles      []AnalysisC2MTile `json:"tiles"`
	Components []struct {
		IFCGlobalID string           `json:"ifcGlobalId"`
		Stats       AnalysisC2MStats `json:"stats"`
	} `json:"components"`
	Global AnalysisC2MStats           `json:"global"`
	Files  map[string]AnalysisC2MFile `json:"files"`
}

func hex64(s string) bool { _, e := hex.DecodeString(s); return e == nil && len(s) == 64 }
func validAnalysisC2MStats(stats AnalysisC2MStats, expected int) bool {
	if stats.KnownCount < 0 || stats.UnknownCount < 0 || stats.KnownCount+stats.UnknownCount != expected {
		return false
	}
	values := []*float64{stats.Min, stats.Max, stats.Mean, stats.Std}
	if stats.KnownCount == 0 {
		return stats.Min == nil && stats.Max == nil && stats.Mean == nil && stats.Std == nil
	}
	for _, value := range values {
		if value == nil || math.IsNaN(*value) || math.IsInf(*value, 0) {
			return false
		}
	}
	return *stats.Min <= *stats.Max && *stats.Std >= 0
}
func ValidateAnalysisC2MManifest(root string, m AnalysisC2MManifest) (int64, error) {
	if m.Schema != "analysis-c2m-result-v1" || !m.Immutable || !hex64(m.ContentHash) || m.UnknownEncoding.Type != "ieee754-float32" || m.UnknownEncoding.Value != "NaN" || m.UnknownEncoding.ByteOrder != "little-endian" || !hex64(m.InputAnalysisMesh.ContentHash) || m.InputAnalysisMesh.ArtifactVersion != "analysis-mesh-artifact-v1" || !validAnalysisModelFrame(m.InputAnalysisMesh.ModelFrame) || m.Algorithm.ID != "c2m-tile-nearest-v1" || m.Algorithm.ImplementationVersion == "" || m.Algorithm.ContractVersion == "" || m.Algorithm.EffectiveParameters == nil || !hex64(m.Scan.ContentHash) || m.Scan.PointsBefore < 1 || m.Scan.PointsAfter < 1 || len(m.Transform) != 16 || len(m.Files) == 0 || len(m.Tiles) == 0 || len(m.Components) == 0 {
		return 0, errors.New("invalid analysis-c2m manifest")
	}
	for _, value := range m.Transform {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return 0, errors.New("invalid analysis-c2m transform")
		}
	}
	seen := map[string]bool{}
	var n int64
	h := sha256.New()
	paths := make([]string, 0, len(m.Files))
	for path := range m.Files {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, p := range paths {
		f := m.Files[p]
		if !hex64(f.SHA256) || f.ByteLength < 0 {
			return 0, errors.New("invalid file")
		}
		q, info, e := analysisMeshArtifactFile(root, p)
		if e != nil || info.Size() != f.ByteLength {
			return 0, errors.New("file size")
		}
		file, e := os.Open(q)
		if e != nil {
			return 0, e
		}
		fileHash := sha256.New()
		_, copyErr := io.Copy(fileHash, file)
		closeErr := file.Close()
		if copyErr != nil || closeErr != nil || hex.EncodeToString(fileHash.Sum(nil)) != f.SHA256 {
			return 0, errors.New("file hash")
		}
		n += f.ByteLength
		h.Write([]byte(p))
		h.Write([]byte{0})
		h.Write([]byte(f.SHA256))
	}
	componentKnown := make(map[string]int, len(m.Components))
	componentUnknown := make(map[string]int, len(m.Components))
	for _, component := range m.Components {
		if strings.TrimSpace(component.IFCGlobalID) == "" {
			return 0, errors.New("component identity")
		}
		if _, duplicate := componentKnown[component.IFCGlobalID]; duplicate {
			return 0, errors.New("duplicate component")
		}
		componentKnown[component.IFCGlobalID] = 0
		componentUnknown[component.IFCGlobalID] = 0
	}
	globalVertices := 0
	seenDistancePaths := make(map[string]bool, len(m.Tiles))
	for _, t := range m.Tiles {
		if t.TileID == "" || strings.ContainsAny(t.TileID, "/\\") || strings.Contains(t.TileID, "..") || seen[t.TileID] || seenDistancePaths[t.DistancePath] || t.IFCGlobalID == "" || t.PartID == "" || !hex64(t.PositionHash) || t.VertexCount < 1 || t.ByteLength != int64(t.VertexCount*4) || !hex64(t.SHA256) || !validAnalysisC2MStats(t.Stats, t.VertexCount) {
			return 0, errors.New("tile binding")
		}
		if _, exists := componentKnown[t.IFCGlobalID]; !exists {
			return 0, errors.New("tile references unknown component")
		}
		f, ok := m.Files[t.DistancePath]
		if !ok || f.SHA256 != t.SHA256 || f.ByteLength != t.ByteLength {
			return 0, errors.New("tile file binding")
		}
		payloadPath, _, pathErr := analysisMeshArtifactFile(root, t.DistancePath)
		if pathErr != nil {
			return 0, errors.New("tile file binding")
		}
		payload, readErr := os.ReadFile(payloadPath)
		if readErr != nil || len(payload) != t.VertexCount*4 || len(payload)%4 != 0 {
			return 0, errors.New("tile file binding")
		}
		seen[t.TileID] = true
		seenDistancePaths[t.DistancePath] = true
		known, unknown := 0, 0
		for offset := 0; offset < len(payload); offset += 4 {
			value := float64(math.Float32frombits(binary.LittleEndian.Uint32(payload[offset : offset+4])))
			if math.IsNaN(value) {
				unknown++
			} else if math.IsInf(value, 0) {
				return 0, errors.New("distance payload contains infinity")
			} else {
				known++
			}
		}
		if known != t.Stats.KnownCount || unknown != t.Stats.UnknownCount {
			return 0, errors.New("tile distance payload statistics mismatch")
		}
		componentKnown[t.IFCGlobalID] += known
		componentUnknown[t.IFCGlobalID] += unknown
		globalVertices += t.VertexCount
	}
	globalKnown, globalUnknown := 0, 0
	for _, component := range m.Components {
		if !validAnalysisC2MStats(component.Stats, componentKnown[component.IFCGlobalID]+componentUnknown[component.IFCGlobalID]) || component.Stats.KnownCount != componentKnown[component.IFCGlobalID] || component.Stats.UnknownCount != componentUnknown[component.IFCGlobalID] {
			return 0, errors.New("component statistics do not match tiles")
		}
		globalKnown += component.Stats.KnownCount
		globalUnknown += component.Stats.UnknownCount
	}
	if !validAnalysisC2MStats(m.Global, globalVertices) || m.Global.KnownCount != globalKnown || m.Global.UnknownCount != globalUnknown {
		return 0, errors.New("global statistics do not match tiles")
	}
	if hex.EncodeToString(h.Sum(nil)) != m.ContentHash {
		return 0, errors.New("content hash")
	}
	b, e := os.ReadFile(filepath.Join(root, "manifest.json"))
	var disk AnalysisC2MManifest
	if e != nil || json.Unmarshal(b, &disk) != nil || !sameJSON(disk, m) {
		return 0, errors.New("manifest disk mismatch")
	}
	return n, nil
}
func AnalysisC2MFingerprint(scanHash, meshHash string, transform []float64, params map[string]any) (string, error) {
	if !hex64(scanHash) || !hex64(meshHash) || len(transform) != 16 {
		return "", errors.New("fingerprint input")
	}
	for _, value := range transform {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return "", errors.New("fingerprint transform")
		}
	}
	b, e := canonicalJSON(struct {
		ScanHash   string         `json:"scanHash"`
		MeshHash   string         `json:"meshHash"`
		Transform  []float64      `json:"transform"`
		Parameters map[string]any `json:"parameters"`
	}{scanHash, meshHash, transform, params})
	return hashBytes([]byte(b)), e
}
func AnalysisC2MPaths(a Asset, v string) (AnalysisMeshPaths, error) {
	if invalidAnalysisMeshVersion(v) {
		return AnalysisMeshPaths{}, errors.New("version")
	}
	rel := filepath.ToSlash(filepath.Join("derivatives", analysisC2MKind, v))
	f, e := safeJoin(a.Dir, rel)
	if e != nil {
		return AnalysisMeshPaths{}, e
	}
	s, e := safeJoin(a.Dir, filepath.Join("derivatives", "."+analysisC2MKind+"-staging", v))
	return AnalysisMeshPaths{RelativePath: rel, FinalPath: f, StagingPath: s}, e
}
func AnalysisC2MDerivativeRow(id int64, p AnalysisMeshPaths, m AnalysisC2MManifest, n int64, fp string) DBAssetDerivative {
	metadata, _ := canonicalJSON(m)
	return DBAssetDerivative{AssetID: id, Kind: analysisC2MKind, Format: "analysis-c2m-v1", Status: "ready", RelativePath: p.RelativePath, EntryPath: "manifest.json", Version: filepath.Base(filepath.FromSlash(p.RelativePath)), ContentHash: m.ContentHash, ByteSize: n, ParamsJSON: fp, MetadataJSON: metadata}
}
func AnalysisC2MVersion(fingerprint string) string {
	if len(fingerprint) > 32 {
		fingerprint = fingerprint[:32]
	}
	return "ac2m-" + fingerprint
}
func PublishAnalysisC2M(staging, final string) error {
	return PublishAnalysisMeshArtifact(staging, final)
}

func analysisC2MResourceDeclared(root, requested, expectedHash, expectedMetadata string) bool {
	relative, err := analysisMeshRelativePath(strings.TrimPrefix(requested, "/"))
	if err != nil {
		return false
	}
	payload, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest AnalysisC2MManifest
	if json.Unmarshal(payload, &manifest) != nil || !strings.EqualFold(manifest.ContentHash, expectedHash) {
		return false
	}
	var stored AnalysisC2MManifest
	if json.Unmarshal([]byte(expectedMetadata), &stored) != nil || !sameJSON(stored, manifest) {
		return false
	}
	if relative == "manifest.json" {
		return true
	}
	_, exists := manifest.Files[relative]
	return exists
}

type analysisC2MJob struct {
	ScanID  int64
	BimID   int64
	OwnerID int64
}

func fileContentHash(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func (a *app) currentAnalysisMesh(asset Asset) (DBAssetDerivative, AnalysisMeshArtifactManifest, string, error) {
	var row DBAssetDerivative
	if err := a.db.Where("asset_id = ? AND kind = ?", asset.ID, analysisMeshKind).First(&row).Error; err != nil || !a.validAnalysisMeshRow(asset, row) {
		return DBAssetDerivative{}, AnalysisMeshArtifactManifest{}, "", errors.New("analysis mesh is not ready")
	}
	root, err := safeJoin(asset.Dir, row.RelativePath)
	if err != nil {
		return DBAssetDerivative{}, AnalysisMeshArtifactManifest{}, "", err
	}
	payload, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return DBAssetDerivative{}, AnalysisMeshArtifactManifest{}, "", err
	}
	var manifest AnalysisMeshArtifactManifest
	if err := json.Unmarshal(payload, &manifest); err != nil {
		return DBAssetDerivative{}, AnalysisMeshArtifactManifest{}, "", err
	}
	return row, manifest, root, nil
}

func (a *app) validAnalysisC2MResult(bim Asset, row DBC2MResult) bool {
	if row.AnalysisStatus != "ready" || row.AnalysisVersion == "" || row.AnalysisRelativePath == "" || !hex64(row.AnalysisContentHash) || !hex64(row.AnalysisMeshHash) || strings.TrimSpace(row.AnalysisMetadataJSON) == "" {
		return false
	}
	root, err := safeJoin(bim.Dir, row.AnalysisRelativePath)
	if err != nil || filepath.Base(root) != row.AnalysisVersion {
		return false
	}
	payload, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest AnalysisC2MManifest
	if json.Unmarshal(payload, &manifest) != nil || manifest.ContentHash != row.AnalysisContentHash || manifest.InputAnalysisMesh.ContentHash != row.AnalysisMeshHash {
		return false
	}
	var stored AnalysisC2MManifest
	if json.Unmarshal([]byte(row.AnalysisMetadataJSON), &stored) != nil || !sameJSON(stored, manifest) {
		return false
	}
	_, err = ValidateAnalysisC2MManifest(root, manifest)
	if err != nil {
		log.Printf("analysis C2M cached artifact validation failed: scan=%d bim=%d version=%s err=%v", row.ScanID, row.BimID, row.AnalysisVersion, err)
		return false
	}
	return true
}

func (a *app) ensureAnalysisC2M(parent context.Context, result DBC2MResult, force bool) (AnalysisC2MManifest, DBC2MResult, bool, error) {
	var scanRow, bimRow DBAsset
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", result.ScanID, result.OwnerID, "pointcloud", "ready").First(&scanRow).Error; err != nil {
		return AnalysisC2MManifest{}, result, false, errors.New("scan asset is not ready")
	}
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", result.BimID, result.OwnerID, "bim", "ready").First(&bimRow).Error; err != nil {
		return AnalysisC2MManifest{}, result, false, errors.New("BIM asset is not ready")
	}
	scan, bim := assetFromDB(scanRow), assetFromDB(bimRow)
	scanPath, err := a.resolveScanSourcePath(scan, result.OwnerID)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	var alignment DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", result.ScanID, result.BimID, result.OwnerID).First(&alignment).Error; err != nil {
		return AnalysisC2MManifest{}, result, false, errors.New("alignment is not ready")
	}
	var transform []float64
	if json.Unmarshal([]byte(alignment.MatrixJSON), &transform) != nil || len(transform) != 16 {
		return AnalysisC2MManifest{}, result, false, errors.New("alignment transform is invalid")
	}
	_, meshManifest, meshRoot, err := a.currentAnalysisMesh(bim)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	scanHash, err := fileContentHash(scanPath)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	voxelSize := result.VoxelSize
	if voxelSize <= 0 {
		voxelSize = 0.02
	}
	parameters := map[string]any{"voxelSize": voxelSize, "coverageMaxDistance": 0.2, "knnK": 1}
	algorithms, err := a.analysisC2MProvider.ListAlgorithms(parent)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	var implementationVersion, contractVersion string
	for _, descriptor := range algorithms {
		if id, _ := descriptor["id"].(string); id == "c2m-tile-nearest-v1" {
			implementationVersion, _ = descriptor["implementationVersion"].(string)
			contractVersion, _ = descriptor["contractVersion"].(string)
			break
		}
	}
	if implementationVersion == "" || contractVersion == "" {
		return AnalysisC2MManifest{}, result, false, errors.New("analysis C2M algorithm identity is unavailable")
	}
	fingerprint, err := AnalysisC2MFingerprint(scanHash, meshManifest.ContentHash, transform, map[string]any{
		"effectiveParameters":   parameters,
		"implementationVersion": implementationVersion,
		"contractVersion":       contractVersion,
	})
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	if !force && result.AnalysisFingerprint == fingerprint && a.validAnalysisC2MResult(bim, result) {
		payload, _ := os.ReadFile(filepath.Join(bim.Dir, result.AnalysisRelativePath, "manifest.json"))
		var manifest AnalysisC2MManifest
		if json.Unmarshal(payload, &manifest) == nil {
			return manifest, result, true, nil
		}
	}
	version := AnalysisC2MVersion(fingerprint)
	if force {
		version = fmt.Sprintf("%s-%d", version, time.Now().UnixNano())
	}
	paths, err := AnalysisC2MPaths(bim, version)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	if _, statErr := os.Stat(paths.FinalPath); statErr == nil {
		version = fmt.Sprintf("%s-%d", version, time.Now().UnixNano())
		paths, err = AnalysisC2MPaths(bim, version)
		if err != nil {
			return AnalysisC2MManifest{}, result, false, err
		}
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return AnalysisC2MManifest{}, result, false, statErr
	}
	if err := os.MkdirAll(filepath.Dir(paths.StagingPath), 0750); err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	defer CleanupAnalysisMeshStaging(paths.StagingPath)
	providerContext, cancel := context.WithTimeout(parent, 2*time.Hour)
	defer cancel()
	manifest, err := func() (AnalysisC2MManifest, error) {
		release, acquireErr := a.acquireMeshProvider(providerContext)
		if acquireErr != nil {
			return AnalysisC2MManifest{}, acquireErr
		}
		defer release()
		return a.analysisC2MProvider.Build(providerContext, AnalysisC2MBuildRequest{
			ScanPath:         meshServicePath(a.cfg.DataDir, scanPath, a.cfg.MeshServiceStorageDir),
			ScanContentHash:  scanHash,
			AnalysisMeshPath: meshServicePath(a.cfg.DataDir, meshRoot, a.cfg.MeshServiceStorageDir),
			OutputPath:       meshServicePath(a.cfg.DataDir, paths.StagingPath, a.cfg.MeshServiceStorageDir),
			Transform:        transform,
			Parameters:       parameters,
			AlgorithmID:      "c2m-tile-nearest-v1",
		})
	}()
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	if manifest.InputAnalysisMesh.ContentHash != meshManifest.ContentHash ||
		!sameJSON(manifest.InputAnalysisMesh.ModelFrame, meshManifest.ModelFrame) ||
		!strings.EqualFold(manifest.Scan.ContentHash, scanHash) ||
		!sameFloat64Slice(manifest.Transform, transform) ||
		!sameJSON(manifest.Algorithm.EffectiveParameters, parameters) ||
		manifest.Algorithm.ID != "c2m-tile-nearest-v1" ||
		manifest.Algorithm.ImplementationVersion != implementationVersion ||
		manifest.Algorithm.ContractVersion != contractVersion ||
		len(manifest.Tiles) != meshManifest.TileCount {
		return AnalysisC2MManifest{}, result, false, errors.New("analysis C2M input mesh binding mismatch")
	}
	byteSize, err := ValidateAnalysisC2MManifest(paths.StagingPath, manifest)
	if err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	if err := PublishAnalysisC2M(paths.StagingPath, paths.FinalPath); err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	manifestMetadata, err := canonicalJSON(manifest)
	if err != nil {
		if removeErr := os.RemoveAll(paths.FinalPath); removeErr != nil {
			log.Printf("analysis C2M metadata rollback cleanup failed: version=%s err=%v", version, removeErr)
		}
		return AnalysisC2MManifest{}, result, false, err
	}
	updates := map[string]any{
		"analysis_status":        "ready",
		"analysis_version":       version,
		"analysis_relative_path": paths.RelativePath,
		"analysis_content_hash":  manifest.ContentHash,
		"analysis_mesh_hash":     meshManifest.ContentHash,
		"analysis_fingerprint":   fingerprint,
		"analysis_metadata_json": manifestMetadata,
		"analysis_error":         nil,
	}
	updated := a.db.Model(&DBC2MResult{}).Where("id = ? AND scan_id = ? AND bim_id = ?", result.ID, result.ScanID, result.BimID).Updates(updates)
	if updated.Error != nil || updated.RowsAffected != 1 {
		if removeErr := os.RemoveAll(paths.FinalPath); removeErr != nil {
			log.Printf("analysis C2M rollback cleanup failed: version=%s err=%v", version, removeErr)
		}
		return AnalysisC2MManifest{}, result, false, errors.New("persist analysis C2M result failed")
	}
	if err := a.db.First(&result, result.ID).Error; err != nil {
		return AnalysisC2MManifest{}, result, false, err
	}
	_ = byteSize // validated package size is reproducible from the manifest files.
	return manifest, result, false, nil
}

func sameFloat64Slice(left, right []float64) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func (a *app) enqueueAnalysisC2M(ctx context.Context, job analysisC2MJob) bool {
	select {
	case <-ctx.Done():
		return false
	case a.meshJobs <- meshBackgroundJob{Kind: meshJobAnalysisC2M, C2M: job}:
		return true
	}
}

func (a *app) processAnalysisC2MForBIM(ctx context.Context, bimID int64) {
	var rows []DBC2MResult
	if err := a.db.Where("bim_id = ?", bimID).Order("updated_at ASC").Find(&rows).Error; err != nil {
		log.Printf("read historical C2M results failed: bim=%d err=%v", bimID, err)
		return
	}
	for _, row := range rows {
		if ctx.Err() != nil {
			return
		}
		a.processAnalysisC2MJob(ctx, analysisC2MJob{ScanID: row.ScanID, BimID: row.BimID, OwnerID: row.OwnerID})
	}
}

func (a *app) processAnalysisC2MJob(parent context.Context, job analysisC2MJob) {
	unlock := a.lockC2MOperation(job.OwnerID, job.ScanID, job.BimID)
	defer unlock()
	var result DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", job.ScanID, job.BimID, job.OwnerID).First(&result).Error; err != nil {
		return
	}
	// GORM mutates the model passed to Updates. Keep the last-ready snapshot for
	// cache validation before exposing the transient processing state to clients.
	cacheCandidate := result
	if err := a.db.Model(&result).Updates(map[string]any{"analysis_status": "processing", "analysis_error": nil}).Error; err != nil {
		log.Printf("analysis C2M status update failed: scan=%d bim=%d status=processing err=%v", job.ScanID, job.BimID, err)
		return
	}
	for attempt := 0; attempt < 12; attempt++ {
		_, updated, _, err := a.ensureAnalysisC2M(parent, cacheCandidate, false)
		if err == nil {
			if statusErr := a.db.Model(&DBC2MResult{}).Where("id = ?", updated.ID).Updates(map[string]any{"analysis_status": "ready", "analysis_error": nil}).Error; statusErr != nil {
				log.Printf("analysis C2M status update failed: scan=%d bim=%d status=ready err=%v", job.ScanID, job.BimID, statusErr)
				return
			}
			log.Printf("analysis C2M recompute complete: scan=%d bim=%d", job.ScanID, job.BimID)
			return
		}
		var providerErr *AnalysisC2MProviderError
		if errors.As(err, &providerErr) && providerErr.Code == "provider_busy" {
			select {
			case <-parent.Done():
				return
			case <-time.After(retryAfterDuration(providerErr.RetryAfter, time.Now(), 5*time.Second)):
				continue
			}
		}
		message := err.Error()
		if statusErr := a.db.Model(&result).Updates(map[string]any{"analysis_status": "failed", "analysis_error": message}).Error; statusErr != nil {
			log.Printf("analysis C2M status update failed: scan=%d bim=%d status=failed err=%v", job.ScanID, job.BimID, statusErr)
		}
		log.Printf("analysis C2M recompute failed: scan=%d bim=%d err=%v", job.ScanID, job.BimID, err)
		return
	}
	message := "analysis C2M provider remained busy after retry limit"
	if statusErr := a.db.Model(&result).Updates(map[string]any{"analysis_status": "failed", "analysis_error": message}).Error; statusErr != nil {
		log.Printf("analysis C2M status update failed: scan=%d bim=%d status=failed err=%v", job.ScanID, job.BimID, statusErr)
	}
	log.Printf("analysis C2M recompute exhausted retries: scan=%d bim=%d", job.ScanID, job.BimID)
}

func (a *app) analysisC2MResponse(result DBC2MResult, manifest AnalysisC2MManifest, cached bool) gin.H {
	base := fmt.Sprintf("/alignments/bim/analysis-c2m/%d/%d/%s/", result.ScanID, result.BimID, url.PathEscape(result.AnalysisVersion))
	return gin.H{"status": result.AnalysisStatus, "version": result.AnalysisVersion, "contentHash": result.AnalysisContentHash, "analysisMeshContentHash": result.AnalysisMeshHash, "manifest": manifest, "baseUrl": base, "manifestUrl": base + "manifest.json", "cached": cached, "updatedAt": result.UpdatedAt}
}

func (a *app) analysisC2MBuild(c *gin.Context) {
	var request struct {
		ScanID int64 `json:"modelScanFileId"`
		BimID  int64 `json:"modelBimFileId"`
		Force  bool  `json:"force"`
	}
	if c.ShouldBindJSON(&request) != nil || request.ScanID <= 0 || request.BimID <= 0 {
		fail(c, http.StatusBadRequest, "invalid_parameters")
		return
	}
	unlock := a.lockC2MOperation(userID(c), request.ScanID, request.BimID)
	defer unlock()
	var result DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", request.ScanID, request.BimID, userID(c)).First(&result).Error; err != nil {
		fail(c, http.StatusConflict, "请先完成一次 C2M 计算")
		return
	}
	manifest, updated, cached, err := a.ensureAnalysisC2M(c.Request.Context(), result, request.Force)
	if err != nil {
		fail(c, http.StatusBadGateway, "analysis_c2m_failed")
		return
	}
	ok(c, a.analysisC2MResponse(updated, manifest, cached))
}

func (a *app) analysisC2MLatest(c *gin.Context) {
	scanID, bimID, valid := c2mPairQuery(c)
	if !valid {
		fail(c, http.StatusBadRequest, "invalid_parameters")
		return
	}
	var result DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, userID(c)).First(&result).Error; err != nil || result.AnalysisStatus != "ready" {
		fail(c, http.StatusNotFound, "analysis_c2m_not_found")
		return
	}
	var bimRow DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", bimID, userID(c)).First(&bimRow).Error; err != nil || !a.validAnalysisC2MResult(assetFromDB(bimRow), result) {
		fail(c, http.StatusNotFound, "analysis_c2m_not_found")
		return
	}
	payload, _ := os.ReadFile(filepath.Join(bimRow.Dir, result.AnalysisRelativePath, "manifest.json"))
	var manifest AnalysisC2MManifest
	_ = json.Unmarshal(payload, &manifest)
	ok(c, a.analysisC2MResponse(result, manifest, false))
}

func (a *app) analysisC2MResource(c *gin.Context) {
	scanID, scanErr := strconv.ParseInt(c.Param("scanId"), 10, 64)
	bimID, bimErr := strconv.ParseInt(c.Param("bimId"), 10, 64)
	if scanErr != nil || bimErr != nil || scanID <= 0 || bimID <= 0 {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	var result DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ? AND analysis_version = ? AND analysis_status = ?", scanID, bimID, userID(c), c.Param("version"), "ready").First(&result).Error; err != nil {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	var bimRow DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", bimID, userID(c)).First(&bimRow).Error; err != nil {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	root, err := safeJoin(bimRow.Dir, result.AnalysisRelativePath)
	if err != nil {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	relative := strings.TrimPrefix(c.Param("path"), "/")
	if relative == "" {
		relative = "manifest.json"
	}
	if !analysisC2MResourceDeclared(root, relative, result.AnalysisContentHash, result.AnalysisMetadataJSON) {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	path, _, err := analysisMeshArtifactFile(root, relative)
	if err != nil {
		fail(c, http.StatusNotFound, "resource_not_found")
		return
	}
	serveAssetFile(c.Writer, c.Request, path)
}
