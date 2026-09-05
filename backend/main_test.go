package main

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSolveRigidFitUsesAllPairs(t *testing.T) {
	pairs := []pair{
		{SX: 0, SY: 0, SZ: 0, BX: 10, BY: -2, BZ: 3},
		{SX: 1, SY: 0, SZ: 0, BX: 11, BY: -2, BZ: 3},
		{SX: 0, SY: 1, SZ: 0, BX: 10, BY: -1, BZ: 3},
		{SX: 0, SY: 0, SZ: 1, BX: 10, BY: -2, BZ: 4},
		{SX: 2, SY: 1, SZ: 3, BX: 12, BY: -1, BZ: 6},
	}
	fit, err := solveRigidFit(pairs)
	if err != nil {
		t.Fatalf("solveRigidFit returned error: %v", err)
	}
	if fit.pairCount != len(pairs) || fit.inlierCount != len(pairs) {
		t.Fatalf("pair counts = %d/%d, want %d/%d", fit.pairCount, fit.inlierCount, len(pairs), len(pairs))
	}
	for _, p := range pairs {
		got := applyRigid(fit, fitPoint{p.SX, p.SY, p.SZ})
		if distance := math.Sqrt((got.x-p.BX)*(got.x-p.BX) + (got.y-p.BY)*(got.y-p.BY) + (got.z-p.BZ)*(got.z-p.BZ)); distance > 1e-8 {
			t.Fatalf("fit error = %g for %+v", distance, p)
		}
	}
}

func TestModelPairJSONMapping(t *testing.T) {
	var pairs []pair
	if err := json.Unmarshal([]byte(`[{"modelScanX":1,"modelScanY":2,"modelScanZ":3,"modelBimX":11,"modelBimY":12,"modelBimZ":13}]`), &pairs); err != nil {
		t.Fatalf("unmarshal model pair: %v", err)
	}
	if len(pairs) != 1 || pairs[0].SX != 1 || pairs[0].SY != 2 || pairs[0].SZ != 3 || pairs[0].BX != 11 || pairs[0].BY != 12 || pairs[0].BZ != 13 {
		t.Fatalf("decoded pair = %+v", pairs)
	}
}

func TestMeshServiceErrorPrefersStructuredMessage(t *testing.T) {
	got := meshServiceError([]byte(`{"code":400,"msg":"LAS 文件不存在: /storage/assets/scan/source"}`))
	want := "LAS 文件不存在: /storage/assets/scan/source"
	if got != want {
		t.Fatalf("meshServiceError() = %q, want %q", got, want)
	}
}

func TestMeshServiceErrorFallsBackToBoundedText(t *testing.T) {
	got := meshServiceError([]byte(" upstream rejected request "))
	if got != "upstream rejected request" {
		t.Fatalf("meshServiceError() = %q, want trimmed upstream text", got)
	}
}

func TestRetryAfterHelpers(t *testing.T) {
	now := time.Unix(1_700_000_000, 0)
	if got := retryAfterDuration("5", now, 3*time.Second); got != 5*time.Second {
		t.Fatalf("delta Retry-After = %s", got)
	}
	if got := retryAfterDuration("", now, 3*time.Second); got != 3*time.Second {
		t.Fatalf("fallback Retry-After = %s", got)
	}
	if got := retryAfterDuration(now.Add(7*time.Second).UTC().Format(http.TimeFormat), now, 3*time.Second); got != 7*time.Second {
		t.Fatalf("date Retry-After = %s", got)
	}
	upstream := &http.Response{Header: http.Header{"Retry-After": []string{"5"}}}
	destination := make(http.Header)
	copyRetryAfter(destination, upstream)
	if destination.Get("Retry-After") != "5" {
		t.Fatalf("forwarded Retry-After = %q", destination.Get("Retry-After"))
	}
}

func TestCallRemeshServiceRetriesBusyResponses(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "model.glb"), []byte("glb"), 0644); err != nil {
		t.Fatal(err)
	}
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempts++
		if attempts < remeshRetryAttempts {
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = w.Write([]byte(`{"code":429,"msg":"busy"}`))
			return
		}
		_, _ = w.Write([]byte("ply"))
	}))
	defer server.Close()

	a := app{cfg: config{MeshServiceURL: server.URL}}
	resp, err := a.callRemeshService(context.Background(), DBAsset{Dir: dir})
	if err != nil {
		t.Fatalf("callRemeshService() error = %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if attempts != remeshRetryAttempts || string(body) != "ply" {
		t.Fatalf("attempts/body = %d/%q", attempts, body)
	}
}

func TestCallRemeshServiceReportsRepeatedBusyResponses(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "model.glb"), []byte("glb"), 0644); err != nil {
		t.Fatal(err)
	}
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempts++
		w.Header().Set("Retry-After", "0")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"code":429,"msg":"still busy"}`))
	}))
	defer server.Close()

	a := app{cfg: config{MeshServiceURL: server.URL}}
	_, err := a.callRemeshService(context.Background(), DBAsset{Dir: dir})
	if err == nil || !strings.Contains(err.Error(), "连续 3 次返回 429") {
		t.Fatalf("busy error = %v", err)
	}
	if attempts != remeshRetryAttempts {
		t.Fatalf("attempts = %d", attempts)
	}
}

func TestMeshServicePathUsesConfiguredStorageRoot(t *testing.T) {
	dataDir := filepath.Join(string(filepath.Separator), "app", "data")
	path := filepath.Join(dataDir, "assets", "scan", "source")
	got := meshServicePath(dataDir, path, "/mnt/cloudbim")
	want := "/mnt/cloudbim/assets/scan/source"
	if got != want {
		t.Fatalf("meshServicePath() = %q, want %q", got, want)
	}
}

func TestMeshServiceInputPathsMapsScanAndMesh(t *testing.T) {
	dataDir := filepath.Join(string(filepath.Separator), "app", "data")
	scanPath := filepath.Join(dataDir, "assets", "scan", "source.las")
	meshPath := filepath.Join(dataDir, "assets", "bim", "mesh_remesh.ply")
	scan, mesh := meshServiceInputPaths(dataDir, "/mnt/cloudbim", scanPath, meshPath)
	if scan != "/mnt/cloudbim/assets/scan/source.las" {
		t.Fatalf("scan service path = %q", scan)
	}
	if mesh != "/mnt/cloudbim/assets/bim/mesh_remesh.ply" {
		t.Fatalf("mesh service path = %q", mesh)
	}
}

func TestC2MProfileAndServiceParams(t *testing.T) {
	if got := normalizeC2MProfile(""); got != "quick" {
		t.Fatalf("empty profile = %q, want quick", got)
	}
	if got := normalizeC2MProfile(" Reference "); got != "reference" {
		t.Fatalf("reference profile = %q", got)
	}
	params := c2mServiceParams(c2mRequest{Profile: "reference", VoxelSize: 0.01, ToleranceLimit: 0.02})
	if params["profile"] != "reference" {
		t.Fatalf("service profile = %#v", params["profile"])
	}
	if params["voxel_size"] != 0.01 || params["tolerance_limit"] != 0.02 {
		t.Fatalf("service params = %#v", params)
	}
	if got, err := resolveC2MResultProfile("", ""); err != nil || got != "quick" {
		t.Fatalf("legacy quick result profile = %q, %v", got, err)
	}
	if _, err := resolveC2MResultProfile("reference", ""); err == nil {
		t.Fatal("reference result without a declared profile was accepted")
	}
	if _, err := resolveC2MResultProfile("reference", "quick"); err == nil {
		t.Fatal("silently downgraded reference result was accepted")
	}
}

func TestC2MServiceResultJSONMapping(t *testing.T) {
	payload := []byte(`{
		"profile":"quick",
		"algorithmVersion":"c2m-quick-v1",
		"metricDirection":"mesh-vertices-to-scan-points",
		"approximation":{"voxelSize":0.05},
		"pointsBefore":100,
		"pointsAfter":25,
		"meshVertices":10,
		"stats":{"min":-0.1,"max":0.2,"mean":0.01,"std":0.02,"p50":0,"p90":0.1,"p95":0.15,"p99":0.19,"meanAbs":0.03,"rmse":0.04,"p95Abs":0.16,"withinToleranceRatio":0.8},
		"histogram":{"counts":[1]},
		"diagnostics":{"bboxOverlapIoU":0.5},
		"coloredPlyPath":"/storage/c2m_results/colored.ply",
		"distancesPath":"/storage/c2m_results/dist.bin"
	}`)
	var result c2mServiceResult
	if err := json.Unmarshal(payload, &result); err != nil {
		t.Fatal(err)
	}
	if result.Profile != "quick" || result.AlgorithmVersion != "c2m-quick-v1" || result.MetricDirection != "mesh-vertices-to-scan-points" {
		t.Fatalf("service metadata = %+v", result)
	}
	if result.Stats.MeanAbs != 0.03 || result.Stats.RMSE != 0.04 || result.Stats.P95Abs != 0.16 || result.Stats.WithinToleranceRatio != 0.8 {
		t.Fatalf("absolute stats = %+v", result.Stats)
	}
	var approximation map[string]float64
	if err := json.Unmarshal(result.Approximation, &approximation); err != nil || approximation["voxelSize"] != 0.05 {
		t.Fatalf("approximation = %s, %v", result.Approximation, err)
	}
}

func TestC2MResultDataPreservesMetadataAndDefaultsLegacyProfile(t *testing.T) {
	row := DBC2MResult{
		ScanID: 1, BimID: 2, VoxelSize: 0.05, PointsBefore: 100, PointsAfter: 25, MeshVertexCount: 10,
		MaxColormapDistance: 0.2, MaxHistogramDistance: 2, HistogramBins: 80, ToleranceLimit: 0.03,
		MeanAbs: 0.03, RMSE: 0.04, P95Abs: 0.16, WithinToleranceRatio: 0.8,
		AlgorithmVersion: "c2m-quick-v1", MetricDirection: "mesh-vertices-to-scan-points",
		HistogramJSON: `{"counts":[1]}`, DiagnosticsJSON: `{"bboxOverlapIoU":0.5}`,
	}
	data := c2mResultData(row)
	encoded, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	var decoded struct {
		Profile          string         `json:"profile"`
		AlgorithmVersion string         `json:"algorithmVersion"`
		MetricDirection  string         `json:"metricDirection"`
		Approximation    map[string]any `json:"approximation"`
		Stats            c2mStats       `json:"stats"`
		Visualization    struct {
			MaxColormapDistance  float64 `json:"maxColormapDistance"`
			MaxHistogramDistance float64 `json:"maxHistogramDistance"`
			HistogramBins        int     `json:"histogramBins"`
			ToleranceLimit       float64 `json:"toleranceLimit"`
		} `json:"visualization"`
	}
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Profile != "quick" || decoded.AlgorithmVersion != row.AlgorithmVersion || decoded.MetricDirection != row.MetricDirection {
		t.Fatalf("result metadata = %+v", decoded)
	}
	if decoded.Approximation["voxelSize"] != 0.05 {
		t.Fatalf("legacy approximation = %+v", decoded.Approximation)
	}
	if decoded.Stats.MeanAbs != 0.03 || decoded.Stats.RMSE != 0.04 || decoded.Stats.P95Abs != 0.16 || decoded.Stats.WithinToleranceRatio != 0.8 {
		t.Fatalf("result stats = %+v", decoded.Stats)
	}
	if decoded.Visualization.MaxColormapDistance != 0.2 || decoded.Visualization.MaxHistogramDistance != 2 || decoded.Visualization.HistogramBins != 80 || decoded.Visualization.ToleranceLimit != 0.03 {
		t.Fatalf("visualization = %+v", decoded.Visualization)
	}
}

func TestC2MResultDataDefaultsHistoricalVisualization(t *testing.T) {
	data := c2mResultData(DBC2MResult{})
	encoded, err := json.Marshal(data["visualization"])
	if err != nil {
		t.Fatal(err)
	}
	var visualization struct {
		MaxColormapDistance  float64 `json:"maxColormapDistance"`
		MaxHistogramDistance float64 `json:"maxHistogramDistance"`
		HistogramBins        int     `json:"histogramBins"`
		ToleranceLimit       float64 `json:"toleranceLimit"`
	}
	if err := json.Unmarshal(encoded, &visualization); err != nil {
		t.Fatal(err)
	}
	if visualization.MaxColormapDistance != 0.10 || visualization.MaxHistogramDistance != 0.10 || visualization.HistogramBins != 50 || visualization.ToleranceLimit != 0.05 {
		t.Fatalf("historical visualization = %+v", visualization)
	}
}

func TestNormalizeC2MRequestRejectsToleranceOutsideColorRange(t *testing.T) {
	req := c2mRequest{MaxColormapDistance: 0.05, MaxHistogramDistance: 0.1, HistogramBins: 50, ToleranceLimit: 0.06}
	if err := normalizeC2MRequest(&req); err == nil {
		t.Fatal("tolerance above color range was accepted")
	}
}

func TestNormalizeC2MRequestUsesCentimeterScaleHistogramDefault(t *testing.T) {
	req := c2mRequest{}
	if err := normalizeC2MRequest(&req); err != nil {
		t.Fatal(err)
	}
	if req.MaxHistogramDistance != 0.10 || req.HistogramBins != 50 {
		t.Fatalf("histogram defaults = range %v bins %d", req.MaxHistogramDistance, req.HistogramBins)
	}
}

func TestC2MInputFingerprintChangesWithInputs(t *testing.T) {
	dir := t.TempDir()
	scanPath := filepath.Join(dir, "scan.las")
	meshPath := filepath.Join(dir, "mesh.ply")
	if err := os.WriteFile(scanPath, []byte("scan"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(meshPath, []byte("mesh"), 0o600); err != nil {
		t.Fatal(err)
	}
	first, err := c2mInputFingerprint("[1,0,0,0]", scanPath, meshPath)
	if err != nil {
		t.Fatal(err)
	}
	second, err := c2mInputFingerprint("[2,0,0,0]", scanPath, meshPath)
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("alignment matrix change did not invalidate fingerprint")
	}
}

func TestC2MArtifactPathRejectsSymlinkEscape(t *testing.T) {
	dataDir := t.TempDir()
	resultsDir := filepath.Join(dataDir, "c2m_results")
	outsideDir := t.TempDir()
	if err := os.MkdirAll(resultsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	outsidePath := filepath.Join(outsideDir, "secret.bin")
	if err := os.WriteFile(outsidePath, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	symlinkPath := filepath.Join(resultsDir, "escaped.bin")
	if err := os.Symlink(outsidePath, symlinkPath); err != nil {
		t.Fatal(err)
	}

	a := app{cfg: config{DataDir: dataDir}}
	if resolved, err := a.c2mArtifactPath(symlinkPath); err == nil {
		t.Fatalf("symlink escape resolved to %q", resolved)
	}
}

func TestC2MResultDataOmitsUnknownAbsoluteStatsForHistoricalRows(t *testing.T) {
	data := c2mResultData(DBC2MResult{MeanAbs: 9, RMSE: 9, P95Abs: 9, WithinToleranceRatio: 9})
	encoded, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	var decoded struct {
		Profile string         `json:"profile"`
		Stats   map[string]any `json:"stats"`
	}
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Profile != "quick" {
		t.Fatalf("historical profile = %q", decoded.Profile)
	}
	for _, key := range []string{"meanAbs", "rmse", "p95Abs", "withinToleranceRatio"} {
		if _, exists := decoded.Stats[key]; exists {
			t.Errorf("historical stats unexpectedly contain %s: %+v", key, decoded.Stats)
		}
	}
}

func TestBackendDataPathUsesConfiguredStorageRoot(t *testing.T) {
	dataDir := filepath.Join(string(filepath.Separator), "app", "data")
	got := backendDataPath(dataDir, "/mnt/cloudbim/c2m_results/result.ply", "/mnt/cloudbim")
	want := filepath.Join(dataDir, "c2m_results", "result.ply")
	if got != want {
		t.Fatalf("backendDataPath() = %q, want %q", got, want)
	}
}

func TestValidExtension(t *testing.T) {
	tests := []struct {
		typ, name string
		want      bool
	}{
		{"bim", "model.ifc", true},
		{"bim", "MODEL.IFC", true},
		{"pointcloud", "scan.las", true},
		{"pointcloud", "scan.laz", false},
		{"bim", "scan.las", false},
	}
	for _, tt := range tests {
		if got := validExtension(tt.typ, tt.name); got != tt.want {
			t.Errorf("validExtension(%q, %q) = %v, want %v", tt.typ, tt.name, got, tt.want)
		}
	}
}

func TestLinkOrSymlinkCreatesReusableLASInput(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "source")
	target := filepath.Join(dir, "source.las")
	if err := os.WriteFile(source, []byte("LAS payload"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := linkOrSymlink(source, target); err != nil {
		t.Fatalf("linkOrSymlink() error = %v", err)
	}
	content, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "LAS payload" {
		t.Fatalf("linked input = %q, want original content", content)
	}
	if err := linkOrSymlink(source, target); err != nil {
		t.Fatalf("linkOrSymlink() should accept an existing target: %v", err)
	}
}

func TestPointcloudColorPattern(t *testing.T) {
	for _, value := range []string{"#ffffff", "#12AbEF", "#000000"} {
		if !pointcloudColorPattern.MatchString(value) {
			t.Errorf("pointcloud color %q should be accepted", value)
		}
	}
	for _, value := range []string{"red", "ffffff", "#fff", "#gggggg", "#1234567"} {
		if pointcloudColorPattern.MatchString(value) {
			t.Errorf("pointcloud color %q should be rejected", value)
		}
	}
}

func TestMeshRemeshSummaryPreservesTaskState(t *testing.T) {
	dir := t.TempDir()
	message := "mesh-service unavailable"
	for _, status := range []string{"idle", "queued", "processing", "failed"} {
		asset := Asset{ID: 7, Type: "bim", Status: "ready", Dir: dir, RemeshStatus: status}
		if status == "failed" {
			asset.RemeshError = &message
		}
		summary := meshRemeshSummary(asset)
		if summary.Status != status {
			t.Fatalf("status %q was exposed as %q", status, summary.Status)
		}
		if status == "failed" && (!summary.CanManualRetry || summary.LastError == nil || *summary.LastError != message) {
			t.Fatalf("failed summary does not expose retry/error: %+v", summary)
		}
	}
}

func TestMeshRemeshSummaryPrefersReadyArtifact(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "mesh_remesh.ply"), []byte("ply"), 0644); err != nil {
		t.Fatal(err)
	}
	asset := Asset{
		ID:                 9,
		Type:               "bim",
		Status:             "ready",
		Dir:                dir,
		RemeshVertexBefore: 20,
		RemeshVertexAfter:  10,
	}
	summary := meshRemeshSummary(asset)
	if summary.Status != "succeeded" || summary.ResultFileID == nil || *summary.ResultFileID != asset.ID {
		t.Fatalf("artifact was not treated as succeeded: %+v", summary)
	}
	if summary.Stats == nil || summary.Stats.VertexBefore != 20 || summary.Stats.VertexAfter != 10 {
		t.Fatalf("summary stats = %+v", summary.Stats)
	}
}

func TestMeshRemeshSummaryDoesNotHideFailedRetryBehindOldArtifact(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "mesh_remesh.ply"), []byte("old ply"), 0644); err != nil {
		t.Fatal(err)
	}
	summary := meshRemeshSummary(Asset{ID: 9, Type: "bim", Status: "ready", Dir: dir, RemeshStatus: "failed"})
	if summary.Status != "failed" || !summary.CanManualRetry {
		t.Fatalf("old artifact hid failed retry state: %+v", summary)
	}
}

func TestSafeJoin(t *testing.T) {
	base := t.TempDir()
	inside, err := safeJoin(base, "tiles/0/1.pnts")
	if err != nil {
		t.Fatalf("safeJoin inside path: %v", err)
	}
	want := filepath.Join(base, "tiles", "0", "1.pnts")
	if inside != want {
		t.Fatalf("safeJoin inside path = %q, want %q", inside, want)
	}
	for _, escape := range []string{"../../etc/passwd", "tiles/../../secret", "../outside-file"} {
		if _, err := safeJoin(base, escape); err == nil {
			t.Errorf("safeJoin(%q) unexpectedly allowed path escape", escape)
		}
	}
}

func TestServeAssetFileHTTPValidatorsAndRange(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "model.glb")
	payload := []byte("0123456789abcdef")
	if err := os.WriteFile(path, payload, 0644); err != nil {
		t.Fatal(err)
	}
	mtime := time.Unix(1_700_000_000, 0)
	if err := os.Chtimes(path, mtime, mtime); err != nil {
		t.Fatal(err)
	}

	get := httptest.NewRecorder()
	serveAssetFile(get, httptest.NewRequest(http.MethodGet, "/assets/1/glb", nil), path)
	if get.Code != http.StatusOK || get.Body.String() != string(payload) {
		t.Fatalf("GET = %d %q", get.Code, get.Body.String())
	}
	if got := get.Header().Get("Cache-Control"); got != legacyAssetCacheControl {
		t.Fatalf("Cache-Control = %q", got)
	}
	if got := get.Header().Get("Vary"); got != "Authorization" {
		t.Fatalf("Vary = %q", got)
	}
	if got := get.Header().Get("Accept-Ranges"); got != "bytes" {
		t.Fatalf("Accept-Ranges = %q", got)
	}
	etag := get.Header().Get("ETag")
	if !strings.HasPrefix(etag, `W/"`) {
		t.Fatalf("ETag = %q", etag)
	}
	lastModified := get.Header().Get("Last-Modified")
	if lastModified == "" {
		t.Fatal("Last-Modified was not set")
	}

	head := httptest.NewRecorder()
	serveAssetFile(head, httptest.NewRequest(http.MethodHead, "/assets/1/glb", nil), path)
	if head.Code != http.StatusOK || head.Body.Len() != 0 {
		t.Fatalf("HEAD = %d with %d body bytes", head.Code, head.Body.Len())
	}
	if head.Header().Get("ETag") != etag || head.Header().Get("Content-Length") != "16" {
		t.Fatalf("HEAD headers = %#v", head.Header())
	}

	rangeRequest := httptest.NewRequest(http.MethodGet, "/assets/1/glb", nil)
	rangeRequest.Header.Set("Range", "bytes=0-3")
	ranged := httptest.NewRecorder()
	serveAssetFile(ranged, rangeRequest, path)
	if ranged.Code != http.StatusPartialContent || ranged.Body.String() != "0123" {
		t.Fatalf("Range = %d %q", ranged.Code, ranged.Body.String())
	}
	if got := ranged.Header().Get("Content-Range"); got != "bytes 0-3/16" {
		t.Fatalf("Content-Range = %q", got)
	}

	notModifiedRequest := httptest.NewRequest(http.MethodGet, "/assets/1/glb", nil)
	notModifiedRequest.Header.Set("If-None-Match", etag)
	notModified := httptest.NewRecorder()
	serveAssetFile(notModified, notModifiedRequest, path)
	if notModified.Code != http.StatusNotModified || notModified.Body.Len() != 0 {
		t.Fatalf("If-None-Match = %d with %d body bytes", notModified.Code, notModified.Body.Len())
	}

	modifiedSinceRequest := httptest.NewRequest(http.MethodGet, "/assets/1/glb", nil)
	modifiedSinceRequest.Header.Set("If-Modified-Since", lastModified)
	modifiedSince := httptest.NewRecorder()
	serveAssetFile(modifiedSince, modifiedSinceRequest, path)
	if modifiedSince.Code != http.StatusNotModified {
		t.Fatalf("If-Modified-Since = %d", modifiedSince.Code)
	}
}

func TestLegacyAssetRepresentations(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "mesh_remesh.ply"), []byte("ply"), 0644); err != nil {
		t.Fatal(err)
	}
	bim := legacyAssetRepresentations(Asset{ID: 4, Type: "bim", Status: "ready", SourceSize: 42, Dir: dir, RemeshStatus: "succeeded"})
	if len(bim) != 3 {
		t.Fatalf("BIM representations = %+v", bim)
	}
	if bim[0].Kind != "browse-detail" || bim[0].URL != "/assets/4/glb" || bim[0].MetadataURL != "/assets/4/metadata" || !bim[0].Legacy {
		t.Fatalf("BIM browse representation = %+v", bim[0])
	}
	if bim[1].Kind != "source" || bim[1].ByteSize != 42 {
		t.Fatalf("BIM source representation = %+v", bim[1])
	}
	if bim[2].Kind != "compute-mesh" || bim[2].Status != "succeeded" || bim[2].URL != "/assets/4/mesh/remesh/latest" {
		t.Fatalf("BIM compute representation = %+v", bim[2])
	}

	pointcloud := legacyAssetRepresentations(Asset{ID: 5, Type: "pointcloud", Status: "ready", SourceSize: 84})
	if len(pointcloud) != 2 {
		t.Fatalf("point-cloud representations = %+v", pointcloud)
	}
	if pointcloud[0].URL != "/assets/5/tiles/tileset.json" || pointcloud[0].BaseURL != "/assets/5/tiles/" {
		t.Fatalf("point-cloud browse representation = %+v", pointcloud[0])
	}
	if pointcloud[1].Kind != "source" || pointcloud[1].Format != "las" || pointcloud[1].ByteSize != 84 {
		t.Fatalf("point-cloud source representation = %+v", pointcloud[1])
	}
}

func TestRepresentationFromDerivative(t *testing.T) {
	message := "conversion failed"
	got := representationFromDerivative(Asset{ID: 7, Dir: t.TempDir()}, DBAssetDerivative{
		Kind: "browse-preview", Format: "3d-tiles", Status: "failed", Version: "v1", ByteSize: 10, ContentHash: "abc", ErrorMessage: &message,
	})
	if got.Kind != "browse-preview" || got.Status != "failed" || got.Version != "v1" || got.ByteSize != 10 || got.ContentHash != "abc" || got.ErrorMessage == nil || *got.ErrorMessage != message || got.Legacy {
		t.Fatalf("derivative representation = %+v", got)
	}
}

func TestMergeAssetRepresentationsReplacesByKindAndKeepsLegacyEntries(t *testing.T) {
	asset := Asset{ID: 5, Type: "pointcloud", Status: "ready", SourceSize: 84, Dir: t.TempDir()}
	rows := []DBAssetDerivative{
		{Kind: "source", Format: "las", Status: "processing"},
		{Kind: "browse-preview", Format: "3d-tiles", Status: "ready", RelativePath: "derivatives/preview", EntryPath: "tiles/tileset.json", Version: "v1"},
	}
	got := mergeAssetRepresentations(asset, rows)
	if len(got) != 3 {
		t.Fatalf("merged representations = %+v", got)
	}
	if got[0].Kind != "browse-detail" || !got[0].Legacy {
		t.Fatalf("legacy browse-detail was lost: %+v", got)
	}
	if got[1].Kind != "source" || got[1].Status != "processing" || got[1].Legacy {
		t.Fatalf("persisted source did not replace legacy source: %+v", got[1])
	}
	if got[2].Kind != "browse-preview" || got[2].URL != "/assets/5/representations/browse-preview/v1/tiles/tileset.json" || got[2].BaseURL != "/assets/5/representations/browse-preview/v1/" {
		t.Fatalf("versioned preview representation = %+v", got[2])
	}
}

func TestDerivativeResourcePathIsContained(t *testing.T) {
	asset := Asset{Dir: t.TempDir()}
	row := DBAssetDerivative{Kind: "browse-preview", Version: "v1", RelativePath: "derivatives/preview", EntryPath: "tileset.json"}
	got, err := derivativeResourcePath(asset, row, "/tiles/0/content.pnts")
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(asset.Dir, "derivatives", "preview", "tiles", "0", "content.pnts")
	if got != want {
		t.Fatalf("derivative path = %q, want %q", got, want)
	}
	if _, err := derivativeResourcePath(asset, row, "../../outside"); err == nil {
		t.Fatal("derivative request path escaped its representation root")
	}
	row.EntryPath = "../outside"
	if _, err := derivativeResourcePath(asset, row, "file"); err == nil {
		t.Fatal("invalid derivative entry path was accepted")
	}
}

func TestLoadConfigProductionRequiresSecret(t *testing.T) {
	keys := []string{"APP_ENV", "JWT_SECRET", "JWT_EXPIRES_IN", "CLOUDBIM_DATA_DIR"}
	values := make(map[string]string)
	for _, key := range keys {
		values[key], _ = os.LookupEnv(key)
		_ = os.Unsetenv(key)
	}
	defer func() {
		for _, key := range keys {
			if values[key] == "" {
				_ = os.Unsetenv(key)
			} else {
				_ = os.Setenv(key, values[key])
			}
		}
	}()
	_ = os.Setenv("APP_ENV", "production")
	if _, err := loadConfig(); err == nil {
		t.Fatal("loadConfig accepted the development JWT secret in production")
	}
}

func TestLoadConfigJWTExpiry(t *testing.T) {
	keys := []string{"APP_ENV", "JWT_SECRET", "JWT_EXPIRES_IN", "CLOUDBIM_DATA_DIR"}
	values := make(map[string]string)
	for _, key := range keys {
		values[key], _ = os.LookupEnv(key)
		_ = os.Unsetenv(key)
	}
	defer func() {
		for _, key := range keys {
			if value, ok := values[key]; ok {
				_ = os.Setenv(key, value)
			} else {
				_ = os.Unsetenv(key)
			}
		}
	}()

	_ = os.Setenv("APP_ENV", "development")
	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig(default JWT expiry) returned error: %v", err)
	}
	if cfg.JWTSecret != developmentJWTSecret {
		t.Fatalf("JWTSecret = %q, want development default", cfg.JWTSecret)
	}
	if cfg.JWTExpiresIn != 30*24*time.Hour {
		t.Fatalf("JWTExpiresIn = %s, want 720h", cfg.JWTExpiresIn)
	}

	_ = os.Setenv("JWT_EXPIRES_IN", "2h30m")
	cfg, err = loadConfig()
	if err != nil {
		t.Fatalf("loadConfig(custom JWT expiry) returned error: %v", err)
	}
	if cfg.JWTExpiresIn != 2*time.Hour+30*time.Minute {
		t.Fatalf("JWTExpiresIn = %s, want 2h30m", cfg.JWTExpiresIn)
	}
	_ = os.Setenv("JWT_EXPIRES_IN", "30d")
	cfg, err = loadConfig()
	if err != nil || cfg.JWTExpiresIn != 30*24*time.Hour {
		t.Fatalf("loadConfig(30d) = %s, %v; want 720h", cfg.JWTExpiresIn, err)
	}

	_ = os.Setenv("JWT_EXPIRES_IN", "0")
	if _, err := loadConfig(); err == nil {
		t.Fatal("loadConfig accepted a non-positive JWT_EXPIRES_IN")
	}
}

func TestLoadConfigMySQL(t *testing.T) {
	keys := []string{"APP_ENV", "DB_DRIVER", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "CLOUDBIM_DATA_DIR"}
	values := make(map[string]string)
	for _, key := range keys {
		values[key], _ = os.LookupEnv(key)
		_ = os.Unsetenv(key)
	}
	defer func() {
		for _, key := range keys {
			if value, ok := values[key]; ok {
				_ = os.Setenv(key, value)
			} else {
				_ = os.Unsetenv(key)
			}
		}
	}()
	_ = os.Setenv("DB_DRIVER", "mysql")
	_ = os.Setenv("DB_HOST", "db.example")
	_ = os.Setenv("DB_USER", "cloudbim")
	_ = os.Setenv("DB_PASSWORD", "secret")
	_ = os.Setenv("DB_NAME", "cloudbim_test")

	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig(mysql) returned error: %v", err)
	}
	if cfg.DBDriver != "mysql" {
		t.Fatalf("DBDriver = %q, want mysql", cfg.DBDriver)
	}
	want := "cloudbim:secret@tcp(db.example:3306)/cloudbim_test?checkConnLiveness=false&loc=Local&parseTime=true&maxAllowedPacket=0&charset=utf8mb4"
	if cfg.DBDSN != want {
		t.Fatalf("DBDSN = %q, want %q", cfg.DBDSN, want)
	}
}

func TestLoadConfigPointcloudSubsample(t *testing.T) {
	t.Setenv("POINTCLOUD_SUBSAMPLE", "0.25")
	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig() error = %v", err)
	}
	if cfg.PointcloudSubsample != 0.25 {
		t.Fatalf("PointcloudSubsample = %v, want 0.25", cfg.PointcloudSubsample)
	}

	t.Setenv("POINTCLOUD_SUBSAMPLE", "1.1")
	if _, err := loadConfig(); err == nil {
		t.Fatal("loadConfig accepted POINTCLOUD_SUBSAMPLE above 1")
	}
}

func TestValidAssetArtifactsRejectsDemoFiles(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "tiles"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "model.glb"), []byte("glTF cloudBIM-viewer backend"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "metadata.json"), []byte(`{"source":"source"}`), 0644); err != nil {
		t.Fatal(err)
	}
	if validAssetArtifacts(DBAsset{Type: "bim", Dir: dir}) {
		t.Fatal("demo BIM artifact was accepted")
	}
	tileset := map[string]any{"root": map[string]any{"content": map[string]any{"uri": "points.pnts"}}}
	tilesetJSON, _ := json.Marshal(tileset)
	if err := os.WriteFile(filepath.Join(dir, "tiles", "tileset.json"), tilesetJSON, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "tiles", "points.pnts"), make([]byte, 188), 0644); err != nil {
		t.Fatal(err)
	}
	if validAssetArtifacts(DBAsset{Type: "pointcloud", Dir: dir}) {
		t.Fatal("demo point-cloud artifact was accepted")
	}
}
