package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const rebarKind = "rebar-segmentation"

type rebarMetadata struct {
	Algorithm struct {
		ID      string `json:"id"`
		Version string `json:"version"`
	} `json:"algorithm"`
	AnalysisSchema      string         `json:"analysisSchema"`
	Capabilities        map[string]any `json:"capabilities"`
	InputOptions        map[string]any `json:"inputOptions"`
	EffectiveParameters map[string]any `json:"effectiveParameters"`
	Summary             map[string]any `json:"summary"`
	Visualization       any            `json:"visualization,omitempty"`
	InputFingerprint    string         `json:"inputFingerprint"`
	RequestFingerprint  string         `json:"requestFingerprint"`
	ResultPath          string         `json:"resultPath"`
}

func canonicalJSON(v any) (string, error) { b, e := json.Marshal(v); return string(b), e }
func hashBytes(b []byte) string           { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func sameJSON(left, right any) bool {
	l, le := canonicalJSON(left)
	r, re := canonicalJSON(right)
	return le == nil && re == nil && l == r
}
func (a *app) rebarLock(id int64) *sync.Mutex {
	a.rebarLocksMu.Lock()
	defer a.rebarLocksMu.Unlock()
	if a.rebarLocks == nil {
		a.rebarLocks = map[int64]*sync.Mutex{}
	}
	if a.rebarLocks[id] == nil {
		a.rebarLocks[id] = &sync.Mutex{}
	}
	return a.rebarLocks[id]
}
func (a *app) rebarAlgorithms(c *gin.Context) {
	v, e := a.rebarProvider.ListAlgorithms(c.Request.Context())
	if e != nil {
		fail(c, 502, "provider_failed")
		return
	}
	ok(c, gin.H{"algorithms": v})
}
func rebarFormat(a Asset) string {
	if e := strings.TrimPrefix(strings.ToLower(filepath.Ext(a.SourceName)), "."); e != "" {
		return e
	}
	return "las"
}
func relRebar(v string) (string, error) {
	if filepath.IsAbs(v) || strings.HasPrefix(filepath.ToSlash(v), "/") {
		return "", errors.New("absolute path")
	}
	if v == "" || filepath.IsAbs(v) || v == "." || v == ".." || strings.HasPrefix(v, "../") || strings.Contains(v, "/../") {
		return "", errors.New("path")
	}
	return filepath.FromSlash(v), nil
}
func rebarFile(root, v string) (string, error) {
	r, e := relRebar(v)
	if e != nil {
		return "", e
	}
	p, e := safeJoin(root, r)
	if e != nil {
		return "", e
	}
	real, e := filepath.EvalSymlinks(p)
	if e != nil {
		return "", e
	}
	rr, e := filepath.EvalSymlinks(root)
	if e != nil {
		return "", e
	}
	q, e := filepath.Rel(rr, real)
	if e != nil || q == ".." || strings.HasPrefix(q, ".."+string(os.PathSeparator)) {
		return "", errors.New("escape")
	}
	return real, nil
}
func rebarManifest(root string, m RebarArtifactManifest) (string, int64, error) {
	if m.Schema != "rebar-artifact-manifest-v1" || m.AnalysisSchema != "rebar-analysis-v1" || m.ArtifactVersion == "" || strings.ContainsAny(m.ArtifactVersion, "/\\") || m.Algorithm.ID == "" {
		return "", 0, errors.New("manifest")
	}
	if m.ManifestPath != "manifest.json" {
		return "", 0, errors.New("manifest path")
	}
	if _, e := rebarFile(root, m.ManifestPath); e != nil {
		return "", 0, e
	}
	manifestBytes, e := os.ReadFile(filepath.Join(root, "manifest.json"))
	if e != nil {
		return "", 0, e
	}
	var disk RebarArtifactManifest
	if json.Unmarshal(manifestBytes, &disk) != nil || !sameJSON(disk, m) {
		return "", 0, errors.New("manifest dto")
	}
	tiles, e := rebarFile(root, m.TilesetPath)
	if e != nil || filepath.ToSlash(m.TilesetPath) != "tiles/tileset.json" {
		return "", 0, errors.New("tiles")
	}
	result, e := rebarFile(root, m.ResultPath)
	if e != nil {
		return "", 0, e
	}
	if info, statErr := os.Stat(result); statErr != nil || !info.Mode().IsRegular() {
		return "", 0, errors.New("result")
	}
	var n int64
	h := sha256.New()
	e = filepath.Walk(root, func(path string, i os.FileInfo, e error) error {
		if e != nil {
			return e
		}
		if i.Mode()&os.ModeSymlink != 0 {
			return errors.New("symlink")
		}
		if i.Mode().IsRegular() {
			n += i.Size()
			if filepath.Clean(path) != filepath.Join(root, "manifest.json") {
				rel, relErr := filepath.Rel(root, path)
				if relErr != nil {
					return relErr
				}
				contents, readErr := os.ReadFile(path)
				if readErr != nil {
					return readErr
				}
				_, _ = h.Write([]byte(filepath.ToSlash(rel)))
				_, _ = h.Write([]byte{0})
				_, _ = h.Write(contents)
			}
		}
		return nil
	})
	if e != nil || n != m.ByteSize || hex.EncodeToString(h.Sum(nil)) != m.ContentHash {
		return "", 0, errors.New("size")
	}
	if i, e := os.Stat(tiles); e != nil || !i.Mode().IsRegular() {
		return "", 0, errors.New("tiles")
	}
	return "tiles/tileset.json", n, nil
}
func rebarFingerprint(a *Asset, source, tiles, request string) (string, error) {
	s, e := os.Stat(source)
	if e != nil {
		return "", e
	}
	t, e := os.Stat(tiles)
	if e != nil {
		return "", e
	}
	h := sha256.New()
	root := filepath.Dir(tiles)
	e = filepath.Walk(root, func(path string, i os.FileInfo, e error) error {
		if e != nil {
			return e
		}
		if !i.Mode().IsRegular() {
			return nil
		}
		rel, re := filepath.Rel(root, path)
		if re != nil {
			return re
		}
		b, re := os.ReadFile(path)
		if re != nil {
			return re
		}
		_, _ = h.Write([]byte(filepath.ToSlash(rel)))
		_, _ = h.Write([]byte{0})
		_, _ = h.Write(b)
		return nil
	})
	if e != nil {
		return "", e
	}
	return hashBytes([]byte(fmt.Sprintf("%d:%d:%d:%d:%d:%s:%s", a.ID, a.SourceSize, s.Size(), s.ModTime().UnixNano(), t.ModTime().UnixNano(), hex.EncodeToString(h.Sum(nil)), request))), nil
}
func (a *app) validRebarRow(asset *Asset, row DBAssetDerivative) bool {
	if row.Status != "ready" {
		return false
	}
	root, e := safeJoin(asset.Dir, row.RelativePath)
	if e != nil {
		return false
	}
	b, e := os.ReadFile(filepath.Join(root, "manifest.json"))
	if e != nil {
		return false
	}
	var m RebarArtifactManifest
	if json.Unmarshal(b, &m) != nil || m.ArtifactVersion != row.Version || m.ContentHash != row.ContentHash {
		return false
	}
	metadata, e := decodeMeta(row)
	if e != nil || m.Algorithm.ID != metadata.Algorithm.ID || m.Algorithm.Version != metadata.Algorithm.Version ||
		m.AnalysisSchema != metadata.AnalysisSchema || m.ResultPath != metadata.ResultPath ||
		m.TilesetPath != row.EntryPath || !sameJSON(m.Capabilities, metadata.Capabilities) ||
		!sameJSON(m.InputOptions, metadata.InputOptions) ||
		!sameJSON(m.EffectiveParameters, metadata.EffectiveParameters) || !sameJSON(m.Summary, metadata.Summary) {
		return false
	}
	if !sameJSON(m.Visualization, metadata.Visualization) {
		return false
	}
	_, size, e := rebarManifest(root, m)
	return e == nil && size == row.ByteSize
}
func decodeMeta(r DBAssetDerivative) (rebarMetadata, error) {
	var m rebarMetadata
	return m, json.Unmarshal([]byte(r.MetadataJSON), &m)
}
func mapAny(v any) map[string]any {
	m, _ := v.(map[string]any)
	if m == nil {
		m = map[string]any{}
	}
	return m
}
func (a *app) rebarResponse(id int64, r DBAssetDerivative, cached bool) gin.H {
	m, _ := decodeMeta(r)
	return gin.H{"assetId": id, "artifactVersion": r.Version, "algorithm": m.Algorithm, "analysisSchema": m.AnalysisSchema, "capabilities": m.Capabilities, "visualization": m.Visualization, "inputOptions": m.InputOptions, "effectiveParameters": m.EffectiveParameters, "summary": m.Summary, "tilesetUrl": fmt.Sprintf("/assets/%d/rebar-segmentation/versions/%s/tiles/tileset.json", id, r.Version), "resultUrl": fmt.Sprintf("/assets/%d/rebar-segmentation/versions/%s/result", id, r.Version), "cached": cached, "updatedAt": r.UpdatedAt}
}
func (a *app) rebarCompute(c *gin.Context) {
	var b struct {
		Algorithm    string         `json:"algorithm"`
		InputOptions map[string]any `json:"inputOptions"`
		Parameters   map[string]any `json:"parameters"`
	}
	if c.ShouldBindJSON(&b) != nil {
		fail(c, 400, "invalid_parameters")
		return
	}
	asset, yes := a.getAsset(c)
	if !yes {
		fail(c, 404, "资产不存在")
		return
	}
	if asset.Type != "pointcloud" || asset.Status != "ready" {
		fail(c, 422, "unsupported_input")
		return
	}
	if b.Algorithm == "" {
		b.Algorithm = "geometric-v3"
	}
	params, _ := canonicalJSON(b)
	lock := a.rebarLock(asset.ID)
	if !lock.TryLock() {
		fail(c, 409, "provider_busy")
		return
	}
	defer lock.Unlock()
	source, e := a.resolveScanSourcePath(*asset, userID(c))
	tiles := filepath.Join(asset.Dir, "tiles", "tileset.json")
	if e != nil {
		fail(c, 422, "unsupported_input")
		return
	}
	fp, e := rebarFingerprint(asset, source, tiles, params)
	if e != nil {
		fail(c, 422, "unsupported_input")
		return
	}
	requestFP := hashBytes([]byte(params))
	var old DBAssetDerivative
	_ = a.db.Where("asset_id=? AND kind=?", asset.ID, rebarKind).First(&old).Error
	if c.Query("force") != "true" && old.ID != 0 {
		if m, e := decodeMeta(old); e == nil && m.InputFingerprint == fp && m.RequestFingerprint == requestFP {
			if a.validRebarRow(asset, old) {
				ok(c, a.rebarResponse(asset.ID, old, true))
				return
			}
		}
	}
	version := fmt.Sprintf("%d-%s", time.Now().UnixNano(), fp[:12])
	base := filepath.Join(asset.Dir, "derivatives", rebarKind)
	stage := filepath.Join(base, ".staging-"+version)
	if e = os.MkdirAll(stage, 0755); e != nil {
		fail(c, 500, "artifact_invalid")
		return
	}
	defer os.RemoveAll(stage)
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Minute)
	defer cancel()
	m, e := a.rebarProvider.Compute(ctx, RebarComputeRequest{meshServicePath(a.cfg.DataDir, source, a.cfg.MeshServiceStorageDir), rebarFormat(*asset), meshServicePath(a.cfg.DataDir, tiles, a.cfg.MeshServiceStorageDir), meshServicePath(a.cfg.DataDir, stage, a.cfg.MeshServiceStorageDir), version, b.Algorithm, b.InputOptions, b.Parameters})
	if e != nil {
		if errors.Is(e, context.DeadlineExceeded) {
			fail(c, http.StatusGatewayTimeout, "provider_timeout")
			return
		}
		if errors.Is(e, context.Canceled) {
			fail(c, 499, "request_canceled")
			return
		}
		var pe *RebarProviderError
		if errors.As(e, &pe) {
			code := pe.Code
			if code == "provider_busy" {
				if pe.RetryAfter != "" {
					c.Header("Retry-After", pe.RetryAfter)
				}
				fail(c, 409, code)
			} else if code == "invalid_parameters" || code == "unsupported_input" || code == "insufficient_evidence" {
				fail(c, 422, code)
			} else if code == "artifact_invalid" {
				fail(c, 502, code)
			} else {
				fail(c, 502, "provider_failed")
			}
		} else {
			fail(c, 502, "provider_failed")
		}
		return
	}
	if m.ArtifactVersion != version {
		fail(c, 502, "artifact_invalid")
		return
	}
	entry, size, e := rebarManifest(stage, m)
	if e != nil {
		fail(c, 502, "artifact_invalid")
		return
	}
	dest := filepath.Join(base, version)
	if e = os.Rename(stage, dest); e != nil {
		fail(c, 500, "artifact_invalid")
		return
	}
	meta := rebarMetadata{AnalysisSchema: m.AnalysisSchema, Capabilities: mapAny(m.Capabilities), Visualization: m.Visualization, InputOptions: mapAny(m.InputOptions), EffectiveParameters: mapAny(m.EffectiveParameters), Summary: mapAny(m.Summary), InputFingerprint: fp, RequestFingerprint: requestFP, ResultPath: m.ResultPath}
	meta.Algorithm.ID, meta.Algorithm.Version = m.Algorithm.ID, m.Algorithm.Version
	raw, _ := canonicalJSON(meta)
	row := DBAssetDerivative{AssetID: asset.ID, Kind: rebarKind, Format: "3dtiles", Status: "ready", RelativePath: filepath.Join("derivatives", rebarKind, version), EntryPath: entry, Version: version, ContentHash: m.ContentHash, ByteSize: size, ParamsJSON: params, MetadataJSON: raw}
	if e = a.db.Transaction(func(tx *gorm.DB) error {
		return tx.Where("asset_id=? AND kind=?", asset.ID, rebarKind).Assign(row).FirstOrCreate(&row).Error
	}); e != nil {
		if cleanupErr := os.RemoveAll(dest); cleanupErr != nil {
			log.Printf("rebar artifact cleanup failed after DB error: asset=%d version=%s err=%v", asset.ID, version, cleanupErr)
		}
		fail(c, 500, "artifact_invalid")
		return
	}
	if old.ID != 0 && old.RelativePath != row.RelativePath {
		if p, e := safeJoin(asset.Dir, old.RelativePath); e == nil {
			if cleanupErr := os.RemoveAll(p); cleanupErr != nil {
				log.Printf("retiring old rebar artifact failed: asset=%d version=%s err=%v", asset.ID, old.Version, cleanupErr)
			}
		}
	}
	ok(c, a.rebarResponse(asset.ID, row, false))
}
func (a *app) rebarLatest(c *gin.Context) {
	x, found := a.getAsset(c)
	if !found {
		fail(c, 404, "资产不存在")
		return
	}
	var r DBAssetDerivative
	if a.db.Where("asset_id=? AND kind=?", x.ID, rebarKind).First(&r).Error != nil {
		fail(c, 404, "资源不存在")
		return
	}
	if !a.validRebarRow(x, r) {
		fail(c, 404, "资源不存在")
		return
	}
	ok(c, a.rebarResponse(x.ID, r, false))
}
func (a *app) rebarResource(c *gin.Context) {
	x, ok := a.getAsset(c)
	if !ok {
		fail(c, 404, "资源不存在")
		return
	}
	var r DBAssetDerivative
	if a.db.Where("asset_id=? AND kind=? AND version=?", x.ID, rebarKind, c.Param("version")).First(&r).Error != nil {
		fail(c, 404, "资源不存在")
		return
	}
	rel := strings.TrimPrefix(c.Param("path"), "/")
	if rel == "" {
		m, e := decodeMeta(r)
		if e != nil {
			fail(c, 404, "资源不存在")
			return
		}
		rel = m.ResultPath
	} else {
		rel = filepath.Join("tiles", rel)
	}
	root, e := safeJoin(x.Dir, r.RelativePath)
	if e != nil {
		fail(c, 404, "资源不存在")
		return
	}
	p, e := rebarFile(root, rel)
	if e != nil {
		fail(c, 404, "资源不存在")
		return
	}
	serveAssetFile(c.Writer, c.Request, p)
}
