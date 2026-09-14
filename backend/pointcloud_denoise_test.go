package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

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
	manifest := denoiseManifest{InstanceContract: "rebar-instance-map-v1", InstancesSHA256: hashBytes([]byte("instance map")), AlgorithmVersion: "test", SourceSHA256: strings.Repeat("a", 64), DesignFingerprint: strings.Repeat("b", 64), PointsBefore: 10, PointsAfter: 4, Counts: map[string]int64{"unknown": 0, "table": 2, "fixture": 2, "steel": 4, "noise": 2}, PreviewPointCount: 10, PreviewOrigin: []float64{0, 0, 0}, NormalK: 32}
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
		os.MkdirAll(req.OutputPath, 0755)
		os.WriteFile(filepath.Join(req.OutputPath, "cleaned.las"), []byte("clean scan"), 0644)
		os.WriteFile(filepath.Join(req.OutputPath, "preview.ply"), []byte("preview"), 0644)
		os.WriteFile(filepath.Join(req.OutputPath, "instance-map.json"), []byte("instance map"), 0644)
		if during != nil {
			during()
		}
		json.NewEncoder(w).Encode(manifest)
	}))
	t.Cleanup(server.Close)
	a.cfg.MeshServiceURL = server.URL
	return &manifest
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
