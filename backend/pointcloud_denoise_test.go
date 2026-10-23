package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func denoiseTestApp(t *testing.T) (*app, Asset) {
	t.Helper()
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open(filepath.Join(root, "test.db")), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err = db.AutoMigrate(&DBAsset{}, &DBAssetDerivative{}, &DBUpload{}, &DBAlignment{}); err != nil {
		t.Fatal(err)
	}
	a := &app{db: db, cfg: config{DataDir: root, MeshServiceStorageDir: root}}
	scan := DBAsset{ID: 1, OwnerID: 10, Type: "pointcloud", Status: "ready", Dir: filepath.Join(root, "scan")}
	bim := DBAsset{ID: 2, OwnerID: 10, Type: "bim", Status: "ready", Dir: filepath.Join(root, "bim")}
	for _, asset := range []DBAsset{scan, bim} {
		if err = db.Create(&asset).Error; err != nil {
			t.Fatal(err)
		}
		if err = os.MkdirAll(asset.Dir, 0755); err != nil {
			t.Fatal(err)
		}
	}
	for path, data := range map[string]string{filepath.Join(scan.Dir, "source.las"): "raw scan", filepath.Join(bim.Dir, "source"): "IFC", filepath.Join(bim.Dir, "model.glb"): "GLB", filepath.Join(bim.Dir, "metadata.json"): "{}"} {
		if err = os.WriteFile(path, []byte(data), 0644); err != nil {
			t.Fatal(err)
		}
	}
	db.Create(&DBUpload{ID: "bim-upload", AssetID: 2, OwnerID: 10, Status: "ready", Dir: bim.Dir})
	db.Create(&DBAlignment{ScanID: 1, BimID: 2, OwnerID: 10, MatrixJSON: `[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]`})
	installPreprocessedScan(t, a, assetFromDB(scan))
	return a, assetFromDB(scan)
}

func installDenoiseService(t *testing.T, a *app, during func()) *denoiseManifest {
	t.Helper()
	controlNet := []byte(`{"schema":"rebar-control-net-evidence-v1","coordinateFrame":"scan","algorithmVersion":"design-control-net-v4","report":{"bodies":[]}}`)
	instanceMap := []byte(`{"schema":"rebar-instance-map-v1","controlNet":{"schema":"rebar-control-net-evidence-v1","coordinateFrame":"scan","algorithmVersion":"design-control-net-v4","report":{"bodies":[]}}}`)
	manifest := denoiseManifest{InstanceContract: "rebar-instance-map-v1", InstancesSHA256: hashBytes(instanceMap), AlgorithmVersion: productionDenoiseAlgorithmPrefix + "test", SourceSHA256: strings.Repeat("a", 64), DesignFingerprint: strings.Repeat("b", 64), PointsBefore: 10, PointsAfter: 4, Counts: map[string]int64{"unknown": 0, "table": 2, "fixture": 2, "steel": 4, "noise": 2}, PreviewPointCount: 10, PreviewOrigin: []float64{0, 0, 0}, NormalK: 32, ControlNetSchema: rebarControlNetEvidenceSchema, ControlNetSHA256: hashBytes(controlNet), ControlNetAlgorithmVersion: "design-control-net-v4"}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			OutputPath, SourcePath, IFCPath, ModelPath string
			Transform                                  []float64
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		if len(req.Transform) != 16 || !strings.HasSuffix(req.SourcePath, "preprocess/test/cleaned.las") || req.IFCPath == "" {
			t.Error("missing source/design/alignment")
		}
		// Match the actual preprocessing bytes, as the production source-integrity gate requires.
		manifest.SourceSHA256, _ = fileContentHash(req.SourcePath)
		os.MkdirAll(req.OutputPath, 0755)
		os.WriteFile(filepath.Join(req.OutputPath, "cleaned.las"), []byte("clean scan"), 0644)
		os.WriteFile(filepath.Join(req.OutputPath, "preview.ply"), []byte("preview"), 0644)
		os.WriteFile(filepath.Join(req.OutputPath, "instance-map.json"), instanceMap, 0644)
		os.WriteFile(filepath.Join(req.OutputPath, "control-net.json"), controlNet, 0644)
		if manifest.ClassificationSchema != "" {
			os.WriteFile(filepath.Join(req.OutputPath, "scene-classification.json"), []byte(`{"schema":"bim-guided-scene-v1"}`), 0644)
		}
		if during != nil {
			during()
		}
		json.NewEncoder(w).Encode(manifest)
	}))
	t.Cleanup(server.Close)
	a.cfg.MeshServiceURL = server.URL
	return &manifest
}

func TestDenoiseSceneCountsAndOptionalPhysicalCounts(t *testing.T) {
	a, _ := denoiseTestApp(t)
	base := *installDenoiseService(t, a, nil)
	if !validDenoiseManifest(base) {
		t.Fatal("historical five-class manifest rejected")
	}
	count := func(value int) *int { return &value }
	cases := []struct {
		name   string
		change func(*denoiseManifest)
		valid  bool
	}{
		{"seven classes", func(m *denoiseManifest) {
			m.Counts["fixture"]--
			m.Counts["noise"]--
			m.Counts["embedded"] = 1
			m.Counts["pipe"] = 1
		}, true},
		{"negative optional class", func(m *denoiseManifest) { m.Counts["pipe"] = -1 }, false},
		{"population mismatch", func(m *denoiseManifest) { m.Counts["embedded"] = 1 }, false},
		{"cleaned remains steel only", func(m *denoiseManifest) { m.Counts["steel"]--; m.Counts["pipe"] = 1 }, false},
		{"physical zero", func(m *denoiseManifest) { m.DesignPhysicalBarCount = count(2); m.MatchedPhysicalBarCount = count(0) }, true},
		{"negative physical count", func(m *denoiseManifest) { m.MatchedPhysicalBarCount = count(-1) }, false},
		{"too many physical matches", func(m *denoiseManifest) { m.DesignPhysicalBarCount = count(2); m.MatchedPhysicalBarCount = count(3) }, false},
		{"too many segment matches", func(m *denoiseManifest) { m.InstanceCount = 2; m.MatchedInstanceCount = count(3) }, false},
		{"missing audit hash", func(m *denoiseManifest) { m.ClassificationSchema = bimGuidedSceneSchema }, false},
		{"missing audit schema", func(m *denoiseManifest) { m.SceneClassificationSHA256 = strings.Repeat("a", 64) }, false},
		{"unknown audit schema", func(m *denoiseManifest) {
			m.ClassificationSchema = "other"
			m.SceneClassificationSHA256 = strings.Repeat("a", 64)
		}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := base
			m.Counts = make(map[string]int64)
			for key, value := range base.Counts {
				m.Counts[key] = value
			}
			tc.change(&m)
			if validDenoiseManifest(m) != tc.valid {
				t.Fatal("unexpected manifest acceptance", tc.valid)
			}
		})
	}
	base.MatchedPhysicalBarCount = count(0)
	raw, err := json.Marshal(base)
	if err != nil || !strings.Contains(string(raw), `"matchedPhysicalBarCount":0`) || strings.Contains(string(raw), `"designPhysicalBarCount"`) {
		t.Fatal("optional counters lost zero or invented historical values", string(raw), err)
	}
}

func TestDenoiseSceneAuditPublishesAndRejectsTampering(t *testing.T) {
	a, scan := denoiseTestApp(t)
	manifest := installDenoiseService(t, a, nil)
	manifest.ClassificationSchema = bimGuidedSceneSchema
	manifest.SceneClassificationSHA256 = hashBytes([]byte(`{"schema":"bim-guided-scene-v1"}`))
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	row, stored, err := a.denoiseRow(scan, 2)
	if err != nil {
		t.Fatal(err)
	}
	c, w = rebarContext("GET", "/?modelScanFileId=1&modelBimFileId=2&version="+row.Version, "", 10)
	c.Params = gin.Params{{Key: "name", Value: "scene-classification.json"}}
	a.denoiseArtifact(c)
	if w.Code != 200 || !strings.Contains(w.Body.String(), bimGuidedSceneSchema) {
		t.Fatal(w.Code, w.Body.String())
	}
	path := filepath.Join(scan.Dir, row.RelativePath, "scene-classification.json")
	if err := os.WriteFile(path, []byte(`{"schema":"tampered"}`), 0644); err != nil {
		t.Fatal(err)
	}
	if err := a.denoiseFresh(scan, 2, 10, row, stored); err == nil || !strings.Contains(err.Error(), "场景分类文件已变化") {
		t.Fatal("tampered audit accepted", err)
	}
	if _, err := a.resolveC2MScanPath(scan, 2, 10); err == nil {
		t.Fatal("C2M accepted tampered scene classification")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := validateStoredSceneClassification(scan, row, stored); err == nil {
		t.Fatal("missing advertised audit accepted")
	}
}

func TestDenoiseRejectsMismatchedSceneAuditBeforePublish(t *testing.T) {
	a, scan := denoiseTestApp(t)
	manifest := installDenoiseService(t, a, nil)
	manifest.ClassificationSchema = bimGuidedSceneSchema
	manifest.SceneClassificationSHA256 = strings.Repeat("a", 64)
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 502 {
		t.Fatal("mismatched audit published", w.Code, w.Body.String())
	}
	if _, _, err := a.denoiseRow(scan, 2); err == nil {
		t.Fatal("invalid result retained")
	}
}

func TestDenoiseFeedsC2MAndRejectsStaleOrForeignResults(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, nil)
	raw, err := a.resolveC2MScanPath(scan, 2, 10)
	if err == nil {
		t.Fatal("C2M accepted an unprocessed scan", raw)
	}
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	row, _, err := a.denoiseRow(scan, 2)
	if err != nil {
		t.Fatal(err)
	}
	cleaned, err := a.resolveC2MScanPath(scan, 2, 10)
	if err != nil || !strings.HasSuffix(cleaned, "cleaned.las") || cleaned == raw {
		t.Fatal(cleaned, err)
	}
	for _, owner := range []int64{10, 11} {
		c, w = rebarContext("GET", "/?modelScanFileId=1&modelBimFileId=2", "", owner)
		a.getDenoiseLatest(c)
		if (owner == 10 && w.Code != 200) || (owner == 11 && w.Code != 404) {
			t.Fatal(owner, w.Code, w.Body.String())
		}
	}
	c, w = rebarContext("GET", "/?modelScanFileId=1&modelBimFileId=2&version=old", "", 10)
	a.denoiseArtifact(c)
	if w.Code != 409 {
		t.Fatal("old version accepted", w.Code)
	}
	a.db.Model(&DBAlignment{}).Where("scan_id = 1").Update("matrix_json", `[1,0,0,0,0,1,0,0,0,0,1,0,1,0,0,1]`)
	if path, err := a.resolveC2MScanPath(scan, 2, 10); err == nil {
		t.Fatal("stale cleanup fell back to raw", path)
	}
	c, w = rebarContext("GET", "/?modelScanFileId=1&modelBimFileId=2", "", 10)
	a.getDenoiseLatest(c)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"fresh":false`) {
		t.Fatal(w.Body.String())
	}
	if _, err = os.Stat(filepath.Join(scan.Dir, row.RelativePath, "cleaned.las")); err != nil {
		t.Fatal("immutable artifact removed")
	}
}

func TestDenoiseDoesNotPublishAfterAlignmentChangesDuringCompute(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, func() {
		a.db.Model(&DBAlignment{}).Where("scan_id=1").Update("matrix_json", `[1,0,0,0,0,1,0,0,0,0,1,0,2,0,0,1]`)
	})
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 409 {
		t.Fatal(w.Code, w.Body.String())
	}
	if _, _, err := a.denoiseRow(scan, 2); err == nil {
		t.Fatal("stale result published")
	}
	files, _ := filepath.Glob(filepath.Join(scan.Dir, "denoise", "2", "*", "cleaned.las"))
	if len(files) != 0 {
		t.Fatal("rejected output leaked")
	}
}

func TestDenoiseDoesNotPublishAfterSourceChangesDuringCompute(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, func() {
		path := filepath.Join(scan.Dir, "preprocess", "test", "cleaned.las")
		file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			t.Error(err)
			return
		}
		_, _ = file.WriteString("changed")
		_ = file.Close()
	})
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != http.StatusConflict {
		t.Fatalf("source mutation status = %d: %s", w.Code, w.Body.String())
	}
	if _, _, err := a.denoiseRow(scan, 2); err == nil {
		t.Fatal("source-stale result published")
	}
}

func TestDenoiseRejectsInvalidPopulation(t *testing.T) {
	a, _ := denoiseTestApp(t)
	manifest := installDenoiseService(t, a, nil)
	manifest.PointsAfter = 0
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 502 {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestC2MRequiresExplicitDenoiseVersion(t *testing.T) {
	a, _ := denoiseTestApp(t)
	for _, body := range []string{`{"modelScanFileId":1,"modelBimFileId":2}`, `{"modelScanFileId":1,"modelBimFileId":2,"denoiseVersion":"missing"}`} {
		c, w := rebarContext("POST", "/", body, 10)
		a.computeC2M(c)
		if w.Code != 409 {
			t.Fatal("C2M accepted missing denoise", w.Code, w.Body.String())
		}
	}
}

func TestDenoiseMissingArtifactDoesNotFallBackToSource(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, nil)
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	path, err := a.resolveC2MScanPath(scan, 2, 10)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if got, err := a.resolveC2MScanPath(scan, 2, 10); err == nil {
		t.Fatal("missing output fell back", got)
	}
}

func TestDenoiseRejectsPreControlVersionAndTamperedControlNet(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, nil)
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	row, manifest, err := a.denoiseRow(scan, 2)
	if err != nil {
		t.Fatal(err)
	}
	legacy := manifest
	legacy.AlgorithmVersion = "denoise-v3"
	if err := a.denoiseFresh(scan, 2, 10, row, legacy); err == nil || !strings.Contains(err.Error(), "控制网生产版本") {
		t.Fatalf("pre-control result freshness = %v", err)
	}
	controlPath := filepath.Join(scan.Dir, row.RelativePath, "control-net.json")
	if err := os.WriteFile(controlPath, []byte(`{"schema":"rebar-control-net-evidence-v1"}`), 0644); err != nil {
		t.Fatal(err)
	}
	if err := a.denoiseFresh(scan, 2, 10, row, manifest); err == nil || !strings.Contains(err.Error(), "控制网文件已变化") {
		t.Fatalf("tampered control net freshness = %v", err)
	}
}
