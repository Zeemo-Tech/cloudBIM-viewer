package main

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
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

func TestMeshServicePathUsesConfiguredStorageRoot(t *testing.T) {
	dataDir := filepath.Join(string(filepath.Separator), "app", "data")
	path := filepath.Join(dataDir, "assets", "scan", "source")
	got := meshServicePath(dataDir, path, "/mnt/cloudbim")
	want := "/mnt/cloudbim/assets/scan/source"
	if got != want {
		t.Fatalf("meshServicePath() = %q, want %q", got, want)
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
