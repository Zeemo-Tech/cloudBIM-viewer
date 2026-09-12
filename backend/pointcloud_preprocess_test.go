package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPreprocessPublishesTilesAtomicallyAndReusesResult(t *testing.T) {
	a, scan := denoiseTestApp(t)
	a.db.Delete(&DBAssetDerivative{}, "asset_id = ? AND kind = ?", scan.ID, tableFreeKind)
	fixtureDir := t.TempDir()
	feature := []byte(`{"POINTS_LENGTH":1}`)
	pnts := make([]byte, 28+len(feature))
	copy(pnts, "pnts")
	binary.LittleEndian.PutUint32(pnts[4:], 1)
	binary.LittleEndian.PutUint32(pnts[8:], uint32(len(pnts)))
	binary.LittleEndian.PutUint32(pnts[12:], uint32(len(feature)))
	copy(pnts[28:], feature)
	if err := os.WriteFile(filepath.Join(fixtureDir, "point.pnts"), pnts, 0644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PREPROCESS_TEST_FIXTURES", fixtureDir)
	script := `#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then shift; output="$1"; fi
  shift
done
mkdir -p "$output"
cp "$PREPROCESS_TEST_FIXTURES/point.pnts" "$output/point.pnts"
printf '%s' '{"root":{"content":{"uri":"point.pnts"}}}' > "$output/tileset.json"
printf '%256s' '' >> "$output/tileset.json"
`
	tool := filepath.Join(fixtureDir, "tiler")
	if err := os.WriteFile(tool, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GOCESIUMTILER_BIN", tool)
	calls := 0
	invalid := true
	var output string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		var request struct{ SourcePath, OutputPath string }
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		if r.URL.Path != "/pointcloud-preprocess/compute" || filepath.Base(request.SourcePath) != "source.las" {
			t.Error("incorrect preprocess contract")
		}
		if calls == 3 {
			up, err := a.findUpload("preprocess-upload")
			if err != nil || up.Status != "processing" {
				t.Error("upload became ready before preprocessing completed")
			}
		}
		output = request.OutputPath
		os.MkdirAll(output, 0755)
		digest, err := fileContentHash(request.SourcePath)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		m := pointcloudPreprocessManifest{AlgorithmVersion: "v1", SourceSHA256: digest, NormalK: 32, PointsBefore: 12, PointsAfter: 10, TablePoints: 2, Detected: true}
		if invalid {
			m.PointsAfter = 0
		}
		for _, name := range []string{"cleaned.las", "annotated.las", "manifest.json"} {
			os.WriteFile(filepath.Join(output, name), []byte("computed"), 0644)
		}
		json.NewEncoder(w).Encode(m)
	}))
	defer server.Close()
	a.cfg.MeshServiceURL = server.URL
	c, w := rebarContext("POST", "/", "", scan.OwnerID)
	a.computePointcloudPreprocess(c)
	if w.Code != 502 {
		t.Fatal(w.Code, w.Body.String())
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatal("rejected artifact leaked", err)
	}
	if _, err := a.resolveScanSourcePath(scan, scan.OwnerID); err == nil {
		t.Fatal("published incomplete output")
	}
	invalid = false
	c, w = rebarContext("POST", "/", "", scan.OwnerID)
	a.computePointcloudPreprocess(c)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	path, err := a.resolveScanSourcePath(scan, scan.OwnerID)
	if err != nil || path != filepath.Join(output, "cleaned.las") {
		t.Fatal(path, err)
	}
	c, w = rebarContext("POST", "/", "", scan.OwnerID)
	a.computePointcloudPreprocess(c)
	if w.Code != 200 || calls != 2 {
		t.Fatal("repeat recomputed", calls, w.Code, w.Body.String())
	}
	c, w = rebarContext("GET", "/", "", scan.OwnerID)
	a.assetRepresentations(c)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"kind":"table-free"`) || !strings.Contains(w.Body.String(), "tiles/tileset.json") {
		t.Fatal(w.Body.String())
	}
	uploadDir := filepath.Join(fixtureDir, "upload")
	if err := os.MkdirAll(uploadDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(uploadDir, "source"), []byte("upload bytes"), 0644); err != nil {
		t.Fatal(err)
	}
	up := DBUpload{ID: "preprocess-upload", AssetType: "pointcloud", FileName: "new.las", FileSize: 12, OwnerID: scan.OwnerID, Status: "queued", Dir: uploadDir}
	if err := a.db.Create(&up).Error; err != nil {
		t.Fatal(err)
	}
	a.processUpload(context.Background(), up.ID)
	loaded, err := a.findUpload(up.ID)
	if err != nil || loaded.Status != "ready" || calls != 3 {
		t.Fatal("upload failed to preprocess", loaded, err, calls)
	}
	var uploaded DBAsset
	if err := a.db.First(&uploaded, loaded.AssetID).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := a.resolveScanSourcePath(assetFromDB(uploaded), scan.OwnerID); err != nil {
		t.Fatal("new upload has no analysis input", err)
	}
}

func installPreprocessedScan(t *testing.T, a *app, scan Asset) DBAssetDerivative {
	t.Helper()
	source, err := a.resolveRawScanSourcePath(scan, scan.OwnerID)
	if err != nil {
		t.Fatal(err)
	}
	fingerprint, err := c2mInputFingerprint("preprocess", source, source)
	if err != nil {
		t.Fatal(err)
	}
	digest, err := fileContentHash(source)
	if err != nil {
		t.Fatal(err)
	}
	manifest := pointcloudPreprocessManifest{AlgorithmVersion: "test", SourceSHA256: digest, InputFingerprint: fingerprint, NormalK: 32, PointsBefore: 12, PointsAfter: 10, TablePoints: 2, Detected: true}
	metadata, _ := json.Marshal(manifest)
	relative := "preprocess/test"
	for _, name := range []string{"annotated.las", "cleaned.las", "manifest.json", "tiles/tileset.json"} {
		path := filepath.Join(scan.Dir, relative, name)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("preprocessed fixture"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	row := DBAssetDerivative{AssetID: scan.ID, Kind: tableFreeKind, Format: "3d-tiles", Status: "ready", RelativePath: relative, EntryPath: "tiles/tileset.json", Version: "test", MetadataJSON: string(metadata)}
	if err := a.db.Create(&row).Error; err != nil {
		t.Fatal(err)
	}
	return row
}

func TestAnalysisUsesTableFreeAndNeverFallsBack(t *testing.T) {
	a, scan := denoiseTestApp(t)
	path, err := a.resolveScanSourcePath(scan, scan.OwnerID)
	if err != nil || !strings.HasSuffix(path, "preprocess/test/cleaned.las") {
		t.Fatal(path, err)
	}
	raw, err := a.resolveRawScanSourcePath(scan, scan.OwnerID)
	if err != nil || !strings.HasSuffix(raw, "source.las") {
		t.Fatal(raw, err)
	}
	if _, err := a.resolveScanSourcePath(scan, 11); err == nil {
		t.Fatal("foreign owner accepted")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, err := a.resolveScanSourcePath(scan, scan.OwnerID); err == nil {
		t.Fatal("missing derivative fell back to original")
	}
}

func TestPreprocessRejectsLegacyAndChangedSource(t *testing.T) {
	a, scan := denoiseTestApp(t)
	if err := os.WriteFile(filepath.Join(scan.Dir, "source.las"), []byte("replacement source"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := a.resolveScanSourcePath(scan, scan.OwnerID); err == nil {
		t.Fatal("stale derivative accepted")
	}
	a.db.Delete(&DBAssetDerivative{}, "asset_id = ? AND kind = ?", scan.ID, tableFreeKind)
	if _, err := a.resolveScanSourcePath(scan, scan.OwnerID); err == nil {
		t.Fatal("legacy scan silently used raw input")
	}
	c, w := rebarContext("GET", "/", "", scan.OwnerID)
	a.getPointcloudPreprocess(c)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"data":null`) {
		t.Fatal(w.Code, w.Body.String())
	}
	c, w = rebarContext("POST", "/", "", 11)
	a.computePointcloudPreprocess(c)
	if w.Code != 404 {
		t.Fatal("foreign preprocessing accepted", w.Code)
	}
}

func TestPreprocessManifestPopulation(t *testing.T) {
	m := pointcloudPreprocessManifest{AlgorithmVersion: "v1", SourceSHA256: strings.Repeat("a", 64), NormalK: 32, PointsBefore: 10, PointsAfter: 10}
	if !validPointcloudPreprocessManifest(m) {
		t.Fatal("no table must be valid")
	}
	m.PointsAfter = 0
	m.TablePoints = 10
	m.Detected = true
	if validPointcloudPreprocessManifest(m) {
		t.Fatal("empty analysis input accepted")
	}
	m.PointsAfter = 8
	m.TablePoints = 1
	if validPointcloudPreprocessManifest(m) {
		t.Fatal("inconsistent population accepted")
	}
}

func TestPreprocessRejectsEqualSizeEqualMtimeSourceReplacement(t *testing.T) {
	a, scan := denoiseTestApp(t)
	source := filepath.Join(scan.Dir, "source.las")
	info, err := os.Stat(source)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(source, []byte("new scan"), 0644); err != nil {
		t.Fatal(err)
	}
	if err = os.Chtimes(source, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	if _, err = a.resolveScanSourcePath(scan, scan.OwnerID); err == nil {
		t.Fatal("equal-size, equal-mtime changed source accepted")
	}
}
