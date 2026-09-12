package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

const analysisMeshKind = "analysis-mesh"
const defaultAnalysisMeshAlgorithm = "rebar-sweep-component-v1"

func analysisMeshProfileFromLegacy(asset DBAsset) (string, map[string]any, error) {
	if err := validateStoredLegacyRemeshArtifact(assetFromDB(asset), false); err != nil {
		return "", nil, err
	}
	if asset.RemeshAlgorithm != defaultRemeshAlgorithm {
		return "", nil, fmt.Errorf("unsupported legacy remesh algorithm %q", asset.RemeshAlgorithm)
	}
	var legacy map[string]any
	if err := json.Unmarshal([]byte(asset.RemeshParamsJSON), &legacy); err != nil || legacy == nil {
		return "", nil, errors.New("legacy remesh parameters are invalid")
	}
	if len(legacy) != 3 {
		return "", nil, errors.New("legacy rebar sweep parameters must contain exactly three values")
	}
	parameters := make(map[string]any, 3)
	for source, target := range map[string]string{
		"cross_section_sides": "crossSectionSides",
		"axial_spacing":       "axialSpacing",
		"max_chord_error":     "maxChordError",
	} {
		value, exists := legacy[source]
		if !exists {
			return "", nil, fmt.Errorf("legacy remesh parameter %s is missing", source)
		}
		parameters[target] = value
	}
	return defaultAnalysisMeshAlgorithm, parameters, nil
}

type AnalysisMeshArtifactFile struct {
	ContentHash string `json:"sha256"`
	ByteSize    int64  `json:"byteLength"`
}
type AnalysisModelFrame struct {
	SourceBounds struct {
		Min []float64 `json:"min"`
		Max []float64 `json:"max"`
	} `json:"sourceBounds"`
	NormalizationCenter []float64 `json:"normalizationCenter"`
}
type AnalysisMeshArtifactManifest struct {
	ArtifactVersion string `json:"artifactVersion"`
	Algorithm       struct {
		ID                    string         `json:"id"`
		ImplementationVersion string         `json:"implementationVersion"`
		ContractVersion       string         `json:"contractVersion"`
		EffectiveParameters   map[string]any `json:"effectiveParameters"`
	} `json:"algorithm"`
	Immutable      bool                                `json:"immutable"`
	ComponentCount int                                 `json:"componentCount"`
	TileCount      int                                 `json:"tileCount"`
	FaceCap        int                                 `json:"faceCap"`
	EntryPath      string                              `json:"entryPath"`
	ComponentsPath string                              `json:"componentsPath"`
	MetricsPath    string                              `json:"metricsPath"`
	ModelFrame     AnalysisModelFrame                  `json:"modelFrame"`
	Files          map[string]AnalysisMeshArtifactFile `json:"files"`
	ContentHash    string                              `json:"contentHash"`
}

type analysisMeshComponents struct {
	Schema     string `json:"schema"`
	Tree       any    `json:"tree"`
	Components []struct {
		IFCGlobalID string `json:"ifcGlobalId"`
		Parts       []struct {
			PartID       string   `json:"partId"`
			NodeName     string   `json:"nodeName"`
			FaceCount    int      `json:"faceCount"`
			PositionHash string   `json:"positionHash"`
			Tiles        []string `json:"tiles"`
		} `json:"parts"`
	} `json:"components"`
	Tiles []struct {
		TileID       string `json:"tileId"`
		URI          string `json:"uri"`
		IFCGlobalID  string `json:"ifcGlobalId"`
		PartID       string `json:"partId"`
		PositionHash string `json:"positionHash"`
		VertexCount  int    `json:"vertexCount"`
		FaceCount    int    `json:"faceCount"`
		ByteLength   int64  `json:"byteLength"`
		SHA256       string `json:"sha256"`
	} `json:"tiles"`
}

func analysisMeshRelativePath(v string) (string, error) {
	v = filepath.ToSlash(strings.TrimSpace(v))
	if v == "" || strings.HasPrefix(v, "/") || filepath.IsAbs(v) || strings.Contains(v, "\\") {
		return "", errors.New("analysis-mesh path is not relative")
	}
	clean := pathClean(v)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return "", errors.New("analysis-mesh path escapes artifact")
	}
	return clean, nil
}
func pathClean(v string) string { return filepath.ToSlash(filepath.Clean(filepath.FromSlash(v))) }

func analysisMeshResourceDeclared(root string, row DBAssetDerivative, requested string) bool {
	relative, err := analysisMeshRelativePath(strings.TrimPrefix(requested, "/"))
	if err != nil {
		return false
	}
	payload, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest AnalysisMeshArtifactManifest
	if json.Unmarshal(payload, &manifest) != nil || !strings.EqualFold(manifest.ContentHash, row.ContentHash) {
		return false
	}
	var metadata struct {
		Manifest AnalysisMeshArtifactManifest `json:"manifest"`
	}
	if json.Unmarshal([]byte(row.MetadataJSON), &metadata) != nil || !sameJSON(metadata.Manifest, manifest) {
		return false
	}
	if relative == "manifest.json" {
		return true
	}
	_, exists := manifest.Files[relative]
	return exists
}

func analysisMeshArtifactFile(root, relative string) (string, os.FileInfo, error) {
	rel, err := analysisMeshRelativePath(relative)
	if err != nil {
		return "", nil, err
	}
	p, err := safeJoin(root, filepath.FromSlash(rel))
	if err != nil {
		return "", nil, err
	}
	info, err := os.Lstat(p)
	if err != nil {
		return "", nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return "", nil, errors.New("analysis-mesh artifact requires regular non-symlink file")
	}
	realRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", nil, err
	}
	realPath, err := filepath.EvalSymlinks(p)
	if err != nil {
		return "", nil, err
	}
	relReal, err := filepath.Rel(realRoot, realPath)
	if err != nil || relReal == ".." || strings.HasPrefix(relReal, ".."+string(os.PathSeparator)) {
		return "", nil, errors.New("analysis-mesh artifact escapes root")
	}
	return p, info, nil
}

// ValidateAnalysisMeshManifest verifies the returned DTO and every declared artifact file.
func ValidateAnalysisMeshManifest(root string, m AnalysisMeshArtifactManifest) (int64, error) {
	if m.ArtifactVersion != "analysis-mesh-artifact-v1" {
		return 0, errors.New("unsupported analysis-mesh artifact version")
	}
	return validateAnalysisMeshV1Manifest(root, m)
}

func validateAnalysisMeshV1Manifest(root string, m AnalysisMeshArtifactManifest) (int64, error) {
	if !m.Immutable || strings.TrimSpace(m.Algorithm.ID) == "" || strings.TrimSpace(m.Algorithm.ImplementationVersion) == "" || strings.TrimSpace(m.Algorithm.ContractVersion) == "" || m.Algorithm.EffectiveParameters == nil || m.ComponentCount < 1 || m.TileCount < 1 || m.FaceCap < 1 || len(m.Files) == 0 || len(m.ContentHash) != 64 || !validAnalysisModelFrame(m.ModelFrame) {
		return 0, errors.New("invalid analysis-mesh v1 manifest")
	}
	if m.EntryPath != "tileset.json" || m.ComponentsPath != "components.json" || m.MetricsPath != "metrics.json" {
		return 0, errors.New("invalid analysis-mesh v1 entry paths")
	}
	for _, required := range []string{m.ComponentsPath, m.MetricsPath, m.EntryPath} {
		if _, ok := m.Files[required]; !ok {
			return 0, fmt.Errorf("analysis-mesh v1 required file missing: %s", required)
		}
	}
	var total int64
	names := make([]string, 0, len(m.Files))
	for rel := range m.Files {
		names = append(names, rel)
	}
	sort.Strings(names)
	aggregate := sha256.New()
	for _, rel := range names {
		file := m.Files[rel]
		if len(file.ContentHash) != 64 || file.ByteSize < 0 {
			return 0, errors.New("invalid analysis-mesh v1 file entry")
		}
		p, info, err := analysisMeshArtifactFile(root, rel)
		if err != nil {
			return 0, err
		}
		if info.Size() != file.ByteSize {
			return 0, fmt.Errorf("analysis-mesh v1 file size mismatch: %s", rel)
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return 0, err
		}
		digest := sha256.Sum256(b)
		if !strings.EqualFold(hex.EncodeToString(digest[:]), file.ContentHash) {
			return 0, fmt.Errorf("analysis-mesh v1 file hash mismatch: %s", rel)
		}
		total += info.Size()
		_, _ = aggregate.Write([]byte(rel))
		_, _ = aggregate.Write([]byte{0})
		_, _ = aggregate.Write([]byte(strings.ToLower(file.ContentHash)))
	}
	if !strings.EqualFold(hex.EncodeToString(aggregate.Sum(nil)), m.ContentHash) {
		return 0, errors.New("analysis-mesh v1 aggregate content hash mismatch")
	}
	componentsBytes, err := os.ReadFile(filepath.Join(root, m.ComponentsPath))
	if err != nil {
		return 0, err
	}
	var components analysisMeshComponents
	if err := json.Unmarshal(componentsBytes, &components); err != nil || components.Schema != "analysis-mesh-components-v1" || len(components.Components) != m.ComponentCount || len(components.Tiles) != m.TileCount || components.Tree == nil {
		return 0, errors.New("invalid analysis-mesh v1 components index")
	}
	componentIDs := make(map[string]struct{}, len(components.Components))
	partOwners := make(map[string]string)
	partTileIDs := make(map[string]map[string]struct{})
	partFaceCounts := make(map[string]int)
	declaredTileOwners := make(map[string]string)
	for _, component := range components.Components {
		if strings.TrimSpace(component.IFCGlobalID) == "" || len(component.Parts) == 0 {
			return 0, errors.New("analysis-mesh component has no IFC GlobalId")
		}
		if _, duplicate := componentIDs[component.IFCGlobalID]; duplicate {
			return 0, errors.New("duplicate analysis-mesh component id")
		}
		componentIDs[component.IFCGlobalID] = struct{}{}
		for _, part := range component.Parts {
			if strings.TrimSpace(part.PartID) == "" || strings.TrimSpace(part.NodeName) == "" || part.FaceCount < 1 || !hex64(part.PositionHash) || len(part.Tiles) == 0 {
				return 0, errors.New("invalid analysis-mesh component part")
			}
			if _, duplicate := partOwners[part.PartID]; duplicate {
				return 0, errors.New("duplicate analysis-mesh part id")
			}
			partOwners[part.PartID] = component.IFCGlobalID
			partFaceCounts[part.PartID] = part.FaceCount
			partTileIDs[part.PartID] = make(map[string]struct{}, len(part.Tiles))
			for _, tileID := range part.Tiles {
				if strings.TrimSpace(tileID) == "" {
					return 0, errors.New("invalid analysis-mesh component part tile id")
				}
				if _, duplicate := partTileIDs[part.PartID][tileID]; duplicate {
					return 0, errors.New("duplicate analysis-mesh component part tile id")
				}
				if _, duplicate := declaredTileOwners[tileID]; duplicate {
					return 0, errors.New("analysis-mesh tile is declared by multiple parts")
				}
				partTileIDs[part.PartID][tileID] = struct{}{}
				declaredTileOwners[tileID] = part.PartID
			}
		}
	}
	treeIDs := make(map[string]struct{})
	if err := collectAnalysisTreeIDs(components.Tree, treeIDs); err != nil {
		return 0, err
	}
	for componentID := range componentIDs {
		if _, exists := treeIDs[componentID]; !exists {
			return 0, fmt.Errorf("analysis-mesh component is absent from IFC tree: %s", componentID)
		}
	}
	tileIDs := make(map[string]struct{}, len(components.Tiles))
	actualPartFaceCounts := make(map[string]int, len(partFaceCounts))
	for _, tile := range components.Tiles {
		declared, exists := m.Files[tile.URI]
		_, knownComponent := componentIDs[tile.IFCGlobalID]
		partOwner, knownPart := partOwners[tile.PartID]
		_, declaredByPart := partTileIDs[tile.PartID][tile.TileID]
		if tile.TileID == "" || tile.PartID == "" || !hex64(tile.PositionHash) || !hex64(tile.SHA256) || tile.VertexCount < 1 || tile.FaceCount < 1 || !exists || !knownComponent || !knownPart || partOwner != tile.IFCGlobalID || !declaredByPart || !strings.EqualFold(declared.ContentHash, tile.SHA256) || declared.ByteSize != tile.ByteLength {
			return 0, errors.New("invalid analysis-mesh v1 tile identity")
		}
		if _, duplicate := tileIDs[tile.TileID]; duplicate {
			return 0, errors.New("duplicate analysis-mesh tile id")
		}
		tileIDs[tile.TileID] = struct{}{}
		actualPartFaceCounts[tile.PartID] += tile.FaceCount
	}
	if len(tileIDs) != len(declaredTileOwners) {
		return 0, errors.New("analysis-mesh part and tile registries differ")
	}
	for tileID := range declaredTileOwners {
		if _, exists := tileIDs[tileID]; !exists {
			return 0, errors.New("analysis-mesh part references an unknown tile")
		}
	}
	for partID, expectedFaces := range partFaceCounts {
		if actualPartFaceCounts[partID] != expectedFaces {
			return 0, errors.New("analysis-mesh part face count differs from its tiles")
		}
	}
	b, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return 0, err
	}
	var persisted AnalysisMeshArtifactManifest
	if json.Unmarshal(b, &persisted) != nil || !sameJSON(persisted, m) {
		return 0, errors.New("analysis-mesh v1 manifest differs from disk")
	}
	return total, nil
}

func validAnalysisModelFrame(frame AnalysisModelFrame) bool {
	if len(frame.SourceBounds.Min) != 3 || len(frame.SourceBounds.Max) != 3 || len(frame.NormalizationCenter) != 3 {
		return false
	}
	for index := 0; index < 3; index++ {
		low, high, center := frame.SourceBounds.Min[index], frame.SourceBounds.Max[index], frame.NormalizationCenter[index]
		if math.IsNaN(low) || math.IsInf(low, 0) || math.IsNaN(high) || math.IsInf(high, 0) || math.IsNaN(center) || math.IsInf(center, 0) || low > high || math.Abs(center-(low+high)/2) > 1e-9 {
			return false
		}
	}
	return true
}

func collectAnalysisTreeIDs(value any, seen map[string]struct{}) error {
	node, ok := value.(map[string]any)
	if !ok {
		return errors.New("analysis-mesh IFC tree node must be an object")
	}
	id, ok := node["id"].(string)
	if !ok || strings.TrimSpace(id) == "" {
		return errors.New("analysis-mesh IFC tree node has no id")
	}
	if _, duplicate := seen[id]; duplicate {
		return errors.New("analysis-mesh IFC tree has duplicate id")
	}
	seen[id] = struct{}{}
	children, ok := node["children"].([]any)
	if !ok {
		return errors.New("analysis-mesh IFC tree children must be an array")
	}
	for _, child := range children {
		if err := collectAnalysisTreeIDs(child, seen); err != nil {
			return err
		}
	}
	return nil
}
func invalidAnalysisMeshVersion(v string) bool {
	return strings.TrimSpace(v) == "" || strings.ContainsAny(v, "/\\") || strings.Contains(v, "..")
}

type AnalysisMeshPaths struct{ RelativePath, StagingPath, FinalPath string }

func AnalysisMeshStoragePaths(asset Asset, artifactVersion string) (AnalysisMeshPaths, error) {
	if invalidAnalysisMeshVersion(artifactVersion) {
		return AnalysisMeshPaths{}, errors.New("invalid analysis-mesh artifact version")
	}
	rel := filepath.ToSlash(filepath.Join("derivatives", analysisMeshKind, artifactVersion))
	final, err := safeJoin(asset.Dir, filepath.FromSlash(rel))
	if err != nil {
		return AnalysisMeshPaths{}, err
	}
	staging, err := safeJoin(asset.Dir, filepath.Join("derivatives", "."+analysisMeshKind+"-staging", artifactVersion))
	if err != nil {
		return AnalysisMeshPaths{}, err
	}
	return AnalysisMeshPaths{RelativePath: rel, StagingPath: staging, FinalPath: final}, nil
}
func AnalysisMeshRequestFingerprint(asset Asset, inputHash, algorithm string, inputOptions, parameters map[string]any) (string, error) {
	if strings.TrimSpace(inputHash) == "" || strings.TrimSpace(algorithm) == "" {
		return "", errors.New("analysis-mesh fingerprint input missing")
	}
	payload := struct {
		AssetID      int64          `json:"assetId"`
		SourceSize   int64          `json:"sourceSize"`
		InputHash    string         `json:"inputHash"`
		Algorithm    string         `json:"algorithm"`
		InputOptions map[string]any `json:"inputOptions"`
		Parameters   map[string]any `json:"parameters"`
	}{asset.ID, asset.SourceSize, inputHash, algorithm, inputOptions, parameters}
	b, err := canonicalJSON(payload)
	if err != nil {
		return "", err
	}
	return hashBytes([]byte(b)), nil
}
func AnalysisMeshArtifactVersion(fingerprint string) string {
	if len(fingerprint) > 32 {
		fingerprint = fingerprint[:32]
	}
	return "am-" + fingerprint
}

func PublishAnalysisMeshArtifact(staging, final string) error {
	if filepath.Clean(staging) == filepath.Clean(final) {
		return errors.New("analysis-mesh staging and final paths match")
	}
	if _, err := os.Stat(final); err == nil {
		return errors.New("analysis-mesh final artifact already exists")
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if info, err := os.Stat(staging); err != nil {
		return err
	} else if !info.IsDir() {
		return errors.New("analysis-mesh staging path is not a directory")
	}
	if err := os.MkdirAll(filepath.Dir(final), 0750); err != nil {
		return err
	}
	return os.Rename(staging, final)
}
func CleanupAnalysisMeshStaging(staging string) error { return os.RemoveAll(staging) }

func AnalysisMeshDerivativeRow(assetID int64, paths AnalysisMeshPaths, m AnalysisMeshArtifactManifest, byteSize int64, requestFingerprint string) (DBAssetDerivative, error) {
	metadata, err := canonicalJSON(struct {
		Manifest           AnalysisMeshArtifactManifest `json:"manifest"`
		RequestFingerprint string                       `json:"requestFingerprint"`
	}{m, requestFingerprint})
	if err != nil {
		return DBAssetDerivative{}, err
	}
	version := filepath.Base(filepath.FromSlash(paths.RelativePath))
	if invalidAnalysisMeshVersion(version) || m.EntryPath == "" {
		return DBAssetDerivative{}, errors.New("invalid analysis-mesh derivative identity")
	}
	return DBAssetDerivative{AssetID: assetID, Kind: analysisMeshKind, Format: "3d-tiles", Status: "ready", RelativePath: paths.RelativePath, EntryPath: m.EntryPath, Version: version, ContentHash: m.ContentHash, ByteSize: byteSize, ParamsJSON: requestFingerprint, MetadataJSON: metadata}, nil
}
func PersistAnalysisMeshDerivative(db *gorm.DB, row DBAssetDerivative) error {
	if db == nil {
		return errors.New("analysis-mesh database is not configured")
	}
	return db.Where("asset_id = ? AND kind = ?", row.AssetID, analysisMeshKind).Assign(row).FirstOrCreate(&DBAssetDerivative{}).Error
}
func AnalysisMeshRepresentation(asset Asset, row DBAssetDerivative) AssetRepresentation {
	return representationFromDerivative(asset, row)
}

func (a *app) analysisMeshLock(assetID int64) *sync.Mutex {
	a.analysisMeshLocksMu.Lock()
	defer a.analysisMeshLocksMu.Unlock()
	if a.analysisMeshLocks == nil {
		a.analysisMeshLocks = make(map[int64]*sync.Mutex)
	}
	if a.analysisMeshLocks[assetID] == nil {
		a.analysisMeshLocks[assetID] = &sync.Mutex{}
	}
	return a.analysisMeshLocks[assetID]
}

func analysisMeshInputHash(modelPath, metadataPath string) (string, error) {
	digest := sha256.New()
	for _, input := range []struct {
		role string
		path string
	}{
		{role: "model.glb", path: modelPath},
		{role: "metadata.json", path: metadataPath},
	} {
		file, err := os.Open(input.path)
		if err != nil {
			return "", err
		}
		fileDigest := sha256.New()
		_, copyErr := io.Copy(fileDigest, file)
		closeErr := file.Close()
		if copyErr != nil {
			return "", copyErr
		}
		if closeErr != nil {
			return "", closeErr
		}
		_, _ = digest.Write([]byte(input.role))
		_, _ = digest.Write([]byte{0})
		_, _ = digest.Write([]byte(hex.EncodeToString(fileDigest.Sum(nil))))
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func (a *app) validAnalysisMeshRow(asset Asset, row DBAssetDerivative) bool {
	if row.Status != "ready" || row.EntryPath != "tileset.json" || row.Version == "" {
		return false
	}
	root, err := safeJoin(asset.Dir, row.RelativePath)
	if err != nil || filepath.Base(root) != row.Version {
		return false
	}
	payload, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest AnalysisMeshArtifactManifest
	if json.Unmarshal(payload, &manifest) != nil || manifest.Algorithm.ID != defaultAnalysisMeshAlgorithm || manifest.ContentHash != row.ContentHash {
		return false
	}
	var stored struct {
		Manifest AnalysisMeshArtifactManifest `json:"manifest"`
	}
	if json.Unmarshal([]byte(row.MetadataJSON), &stored) != nil || !sameJSON(stored.Manifest, manifest) {
		return false
	}
	byteSize, err := ValidateAnalysisMeshManifest(root, manifest)
	return err == nil && byteSize == row.ByteSize
}

func (a *app) analysisMeshResponse(asset Asset, row DBAssetDerivative, cached bool) gin.H {
	representation := AnalysisMeshRepresentation(asset, row)
	return gin.H{
		"assetId":        asset.ID,
		"version":        row.Version,
		"contentHash":    row.ContentHash,
		"representation": representation,
		"manifestUrl":    representation.BaseURL + "manifest.json",
		"componentsUrl":  representation.BaseURL + "components.json",
		"tilesetUrl":     representation.URL,
		"cached":         cached,
		"updatedAt":      row.UpdatedAt,
	}
}

func (a *app) analysisMeshAlgorithms(c *gin.Context) {
	if a.cfg.MeshServiceURL == "" {
		fail(c, http.StatusServiceUnavailable, "analysis_mesh_not_configured")
		return
	}
	algorithms, err := a.analysisMeshProvider.ListAlgorithms(c.Request.Context())
	if err != nil {
		fail(c, http.StatusBadGateway, "analysis_mesh_provider_failed")
		return
	}
	ok(c, gin.H{"algorithms": algorithms})
}

func (a *app) ensureAnalysisMesh(parent context.Context, asset Asset, algorithmID string, parameters map[string]any, faceCap int, force bool) (DBAssetDerivative, bool, error) {
	lock := a.analysisMeshLock(asset.ID)
	lock.Lock()
	defer lock.Unlock()

	modelPath := filepath.Join(asset.Dir, "model.glb")
	metadataPath := filepath.Join(asset.Dir, "metadata.json")
	inputHash, err := analysisMeshInputHash(modelPath, metadataPath)
	if err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_input_invalid: %w", err)
	}
	algorithms, err := a.analysisMeshProvider.ListAlgorithms(parent)
	if err != nil {
		return DBAssetDerivative{}, false, err
	}
	var selected *AnalysisMeshAlgorithmDescriptor
	for index := range algorithms {
		if algorithms[index].ID == algorithmID {
			selected = &algorithms[index]
			break
		}
	}
	if selected == nil {
		return DBAssetDerivative{}, false, fmt.Errorf("invalid_parameters: unknown analysis-mesh algorithm %q", algorithmID)
	}
	effectiveParameters := make(map[string]any, len(selected.Defaults)+len(parameters))
	for name, value := range selected.Defaults {
		effectiveParameters[name] = value
	}
	for name, value := range parameters {
		effectiveParameters[name] = value
	}
	fingerprint, err := AnalysisMeshRequestFingerprint(asset, inputHash, algorithmID, map[string]any{
		"faceCap":               faceCap,
		"implementationVersion": selected.ImplementationVersion,
		"contractVersion":       selected.ContractVersion,
	}, effectiveParameters)
	if err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("invalid_parameters: %w", err)
	}
	var old DBAssetDerivative
	_ = a.db.Where("asset_id = ? AND kind = ?", asset.ID, analysisMeshKind).First(&old).Error
	if !force && old.ID != 0 && old.ParamsJSON == fingerprint && a.validAnalysisMeshRow(asset, old) {
		return old, true, nil
	}

	version := AnalysisMeshArtifactVersion(fingerprint)
	if force {
		version = fmt.Sprintf("%s-%d", version, time.Now().UnixNano())
	}
	paths, err := AnalysisMeshStoragePaths(asset, version)
	if err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	if _, statErr := os.Stat(paths.FinalPath); statErr == nil {
		version = fmt.Sprintf("%s-%d", version, time.Now().UnixNano())
		paths, err = AnalysisMeshStoragePaths(asset, version)
		if err != nil {
			return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
		}
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", statErr)
	}
	if err := os.MkdirAll(filepath.Dir(paths.StagingPath), 0750); err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	defer func() {
		if cleanupErr := CleanupAnalysisMeshStaging(paths.StagingPath); cleanupErr != nil {
			log.Printf("analysis mesh staging cleanup failed: asset=%d err=%v", asset.ID, cleanupErr)
		}
	}()
	providerContext, cancel := context.WithTimeout(parent, 2*time.Hour)
	defer cancel()
	manifest, err := func() (AnalysisMeshArtifactManifest, error) {
		release, acquireErr := a.acquireMeshProvider(providerContext)
		if acquireErr != nil {
			return AnalysisMeshArtifactManifest{}, acquireErr
		}
		defer release()
		return a.analysisMeshProvider.Build(providerContext, AnalysisMeshBuildRequest{
			ModelPath:    meshServicePath(a.cfg.DataDir, modelPath, a.cfg.MeshServiceStorageDir),
			MetadataPath: meshServicePath(a.cfg.DataDir, metadataPath, a.cfg.MeshServiceStorageDir),
			OutputPath:   meshServicePath(a.cfg.DataDir, paths.StagingPath, a.cfg.MeshServiceStorageDir),
			AlgorithmID:  algorithmID,
			Parameters:   effectiveParameters,
			FaceCap:      faceCap,
		})
	}()
	if err != nil {
		return DBAssetDerivative{}, false, err
	}
	if manifest.Algorithm.ID != selected.ID ||
		manifest.Algorithm.ImplementationVersion != selected.ImplementationVersion ||
		manifest.Algorithm.ContractVersion != selected.ContractVersion ||
		!sameJSON(manifest.Algorithm.EffectiveParameters, effectiveParameters) {
		return DBAssetDerivative{}, false, errors.New("analysis_mesh_artifact_invalid: provider algorithm identity mismatch")
	}
	byteSize, err := ValidateAnalysisMeshManifest(paths.StagingPath, manifest)
	if err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	if err := PublishAnalysisMeshArtifact(paths.StagingPath, paths.FinalPath); err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	row, err := AnalysisMeshDerivativeRow(asset.ID, paths, manifest, byteSize, fingerprint)
	if err != nil || PersistAnalysisMeshDerivative(a.db, row) != nil {
		if removeErr := os.RemoveAll(paths.FinalPath); removeErr != nil {
			log.Printf("analysis mesh rollback cleanup failed: asset=%d version=%s err=%v", asset.ID, version, removeErr)
		}
		if err == nil {
			err = errors.New("persist analysis mesh derivative failed")
		}
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	var saved DBAssetDerivative
	if err := a.db.Where("asset_id = ? AND kind = ?", asset.ID, analysisMeshKind).First(&saved).Error; err != nil {
		return DBAssetDerivative{}, false, fmt.Errorf("analysis_mesh_artifact_invalid: %w", err)
	}
	return saved, false, nil
}

func (a *app) analysisMeshBuild(c *gin.Context) {
	asset, found := a.meshAsset(c)
	if !found {
		return
	}
	if a.cfg.MeshServiceURL == "" {
		fail(c, http.StatusServiceUnavailable, "analysis_mesh_not_configured")
		return
	}
	var request struct {
		AlgorithmID string         `json:"algorithmId"`
		Parameters  map[string]any `json:"parameters"`
		FaceCap     int            `json:"faceCap"`
		Force       bool           `json:"force"`
	}
	if c.ShouldBindJSON(&request) != nil {
		fail(c, http.StatusBadRequest, "invalid_parameters")
		return
	}
	if request.AlgorithmID == "" {
		request.AlgorithmID = defaultAnalysisMeshAlgorithm
	}
	if request.FaceCap == 0 {
		request.FaceCap = 250000
	}
	if request.FaceCap < 1 || request.FaceCap > 250000 {
		fail(c, http.StatusBadRequest, "invalid_parameters")
		return
	}
	row, cached, err := a.ensureAnalysisMesh(c.Request.Context(), asset, request.AlgorithmID, request.Parameters, request.FaceCap, request.Force)
	if err != nil {
		var providerErr *AnalysisMeshProviderError
		if errors.As(err, &providerErr) && providerErr.Code == "provider_busy" {
			if providerErr.RetryAfter != "" {
				c.Header("Retry-After", providerErr.RetryAfter)
			}
			fail(c, http.StatusConflict, "analysis_mesh_provider_busy")
			return
		}
		if errors.Is(err, context.DeadlineExceeded) {
			fail(c, http.StatusGatewayTimeout, "analysis_mesh_provider_timeout")
			return
		}
		fail(c, http.StatusBadGateway, "analysis_mesh_provider_failed")
		return
	}
	ok(c, a.analysisMeshResponse(asset, row, cached))
}

func (a *app) analysisMeshLatest(c *gin.Context) {
	asset, found := a.meshAsset(c)
	if !found {
		return
	}
	var row DBAssetDerivative
	if a.db.Where("asset_id = ? AND kind = ?", asset.ID, analysisMeshKind).First(&row).Error != nil || !a.validAnalysisMeshRow(asset, row) {
		fail(c, http.StatusNotFound, "analysis_mesh_not_found")
		return
	}
	ok(c, a.analysisMeshResponse(asset, row, false))
}

func (a *app) processAnalysisMeshJob(parent context.Context, assetID int64) {
	if a.cfg.MeshServiceURL == "" {
		return
	}
	var row DBAsset
	if err := a.db.Where("id = ? AND type = ? AND status = ?", assetID, "bim", "ready").First(&row).Error; err != nil {
		return
	}
	asset := assetFromDB(row)
	algorithmID, parameters, err := analysisMeshProfileFromLegacy(row)
	if err != nil {
		log.Printf("BIM 资产 %d analysis-mesh 跳过：legacy remesh 身份无效: %v", assetID, err)
		return
	}
	for attempt := 0; attempt < 12; attempt++ {
		_, _, err = a.ensureAnalysisMesh(parent, asset, algorithmID, parameters, 250000, false)
		if err == nil {
			log.Printf("BIM 资产 %d analysis-mesh 构建完成", assetID)
			a.processAnalysisC2MForBIM(parent, assetID)
			return
		}
		var providerErr *AnalysisMeshProviderError
		if errors.As(err, &providerErr) && providerErr.Code == "provider_busy" {
			delay := retryAfterDuration(providerErr.RetryAfter, time.Now(), 5*time.Second)
			select {
			case <-parent.Done():
				return
			case <-time.After(delay):
				continue
			}
		}
		message := err.Error()
		var current DBAssetDerivative
		if a.db.Where("asset_id = ? AND kind = ?", assetID, analysisMeshKind).First(&current).Error != nil || !a.validAnalysisMeshRow(asset, current) {
			failed := DBAssetDerivative{AssetID: assetID, Kind: analysisMeshKind, Format: "3d-tiles", Status: "failed", ErrorMessage: &message}
			_ = a.db.Where("asset_id = ? AND kind = ?", assetID, analysisMeshKind).Assign(failed).FirstOrCreate(&DBAssetDerivative{}).Error
		}
		log.Printf("BIM 资产 %d analysis-mesh 构建失败: %v", assetID, err)
		return
	}
	message := "analysis-mesh provider remained busy after retry limit"
	var current DBAssetDerivative
	if a.db.Where("asset_id = ? AND kind = ?", assetID, analysisMeshKind).First(&current).Error != nil || !a.validAnalysisMeshRow(asset, current) {
		failed := DBAssetDerivative{AssetID: assetID, Kind: analysisMeshKind, Format: "3d-tiles", Status: "failed", ErrorMessage: &message}
		_ = a.db.Where("asset_id = ? AND kind = ?", assetID, analysisMeshKind).Assign(failed).FirstOrCreate(&DBAssetDerivative{}).Error
	}
	log.Printf("BIM 资产 %d analysis-mesh 构建重试耗尽", assetID)
}

func AnalysisMeshAlgorithmsHandler(provider AnalysisMeshProvider) gin.HandlerFunc {
	return func(c *gin.Context) {
		algorithms, err := provider.ListAlgorithms(c.Request.Context())
		if err != nil {
			fail(c, http.StatusBadGateway, err.Error())
			return
		}
		ok(c, gin.H{"algorithms": algorithms})
	}
}

type AnalysisMeshJobStatus string

const (
	AnalysisMeshQueued     AnalysisMeshJobStatus = "queued"
	AnalysisMeshProcessing AnalysisMeshJobStatus = "processing"
	AnalysisMeshReady      AnalysisMeshJobStatus = "ready"
	AnalysisMeshFailed     AnalysisMeshJobStatus = "failed"
)

type AnalysisMeshJob struct {
	Status                          AnalysisMeshJobStatus
	Attempts                        int
	ErrorMessage                    string
	QueuedAt, StartedAt, FinishedAt *time.Time
}

func (j *AnalysisMeshJob) Transition(next AnalysisMeshJobStatus, now time.Time) error {
	valid := (j.Status == "" && next == AnalysisMeshQueued) || (j.Status == AnalysisMeshQueued && (next == AnalysisMeshProcessing || next == AnalysisMeshFailed)) || (j.Status == AnalysisMeshProcessing && (next == AnalysisMeshReady || next == AnalysisMeshFailed)) || (j.Status == AnalysisMeshFailed && next == AnalysisMeshQueued)
	if !valid {
		return fmt.Errorf("invalid analysis-mesh job transition: %s -> %s", j.Status, next)
	}
	j.Status = next
	switch next {
	case AnalysisMeshQueued:
		j.Attempts++
		j.QueuedAt = &now
		j.StartedAt = nil
		j.FinishedAt = nil
		j.ErrorMessage = ""
	case AnalysisMeshProcessing:
		j.StartedAt = &now
	case AnalysisMeshReady, AnalysisMeshFailed:
		j.FinishedAt = &now
	}
	return nil
}
