package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMetricSourceFeedsTilesAndPreprocessWithoutChangingUpload(t *testing.T) {
	a, scan := denoiseTestApp(t)
	raw := metricLASFixture(t, 2, "millimeters")
	// Preprocessing requires at least three points; add a duplicate point without
	// changing this fixture's bounds or its declared units.
	pointOffset := int(binary.LittleEndian.Uint32(raw[96:100]))
	pointLength := int(binary.LittleEndian.Uint16(raw[105:107]))
	raw = append(raw, raw[pointOffset:pointOffset+pointLength]...)
	binary.LittleEndian.PutUint32(raw[107:111], 3)
	binary.LittleEndian.PutUint32(raw[111:115], 3)
	rawPath := filepath.Join(scan.Dir, "source.las")
	if err := os.WriteFile(rawPath, raw, 0644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := a.pointcloudPreprocessRow(scan); err == nil {
		t.Fatal("old millimetre derivative accepted")
	}
	fixtureDir := t.TempDir()
	feature := []byte(`{"POINTS_LENGTH":3}`)
	pnts := make([]byte, 28+len(feature))
	copy(pnts, "pnts")
	binary.LittleEndian.PutUint32(pnts[4:], 1)
	binary.LittleEndian.PutUint32(pnts[8:], uint32(len(pnts)))
	binary.LittleEndian.PutUint32(pnts[12:], uint32(len(feature)))
	copy(pnts[28:], feature)
	if err := os.WriteFile(filepath.Join(fixtureDir, "point.pnts"), pnts, 0644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("METRIC_TILER_FIXTURES", fixtureDir)
	logPath := filepath.Join(fixtureDir, "tiler-inputs")
	t.Setenv("METRIC_TILER_INPUTS", logPath)
	tool := filepath.Join(fixtureDir, "tiler")
	script := `#!/bin/sh
while [ "$#" -gt 0 ]; do
  input="$1"
  if [ "$1" = "-o" ]; then shift; output="$1"; fi
  shift
done
printf '%s\n' "$input" >> "$METRIC_TILER_INPUTS"
mkdir -p "$output"
cp "$METRIC_TILER_FIXTURES/point.pnts" "$output/point.pnts"
printf '%s' '{"root":{"content":{"uri":"point.pnts"}}}' > "$output/tileset.json"
printf '%256s' '' >> "$output/tileset.json"
`
	if err := os.WriteFile(tool, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GOCESIUMTILER_BIN", tool)
	if err := buildPointCloud(context.Background(), rawPath, scan.Dir, .25); err != nil {
		t.Fatal(err)
	}
	calls := 0
	mutateDuringCompute := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		var req struct{ SourcePath, OutputPath string }
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		data, err := os.ReadFile(req.SourcePath)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		if req.SourcePath == rawPath || math.Abs(math.Float64frombits(binary.LittleEndian.Uint64(data[131:139]))-.000001) > 1e-15 {
			t.Error("mesh service did not receive metre coordinates")
		}
		if err := os.MkdirAll(req.OutputPath, 0755); err != nil {
			t.Error(err)
			w.WriteHeader(500)
			return
		}
		for _, name := range []string{"cleaned.las", "annotated.las"} {
			if err := os.WriteFile(filepath.Join(req.OutputPath, name), data, 0644); err != nil {
				t.Error(err)
			}
		}
		digest, _ := fileContentHash(req.SourcePath)
		m := pointcloudPreprocessManifest{AlgorithmVersion: "metric-test", SourceSHA256: digest, NormalK: 32, PointsBefore: 3, PointsAfter: 3}
		encoded, _ := json.Marshal(m)
		os.WriteFile(filepath.Join(req.OutputPath, "manifest.json"), encoded, 0644)
		if mutateDuringCompute {
			changed, err := os.ReadFile(rawPath)
			if err != nil {
				t.Error(err)
			} else {
				changed[len(changed)-2] ^= 1
				if err := os.WriteFile(rawPath, changed, 0644); err != nil {
					t.Error(err)
				}
			}
		}
		w.Write(encoded)
	}))
	defer server.Close()
	a.cfg.MeshServiceURL = server.URL
	row, result, err := a.buildPointcloudPreprocess(context.Background(), scan)
	if err != nil {
		t.Fatal(err)
	}
	if row.Version == "test" || result.SourceSHA256 == hashBytes(raw) {
		t.Fatal("stale millimetre artifact was reused")
	}
	if _, _, err := a.pointcloudPreprocessRow(scan); err != nil {
		t.Fatal("new metric artifact rejected", err)
	}
	if _, _, err := a.buildPointcloudPreprocess(context.Background(), scan); err != nil || calls != 1 {
		t.Fatal("metric cache was not reused", err, calls)
	}
	inputs, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	paths := strings.Fields(string(inputs))
	if len(paths) != 2 {
		t.Fatalf("tiler calls: %v", paths)
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		scale := math.Float64frombits(binary.LittleEndian.Uint64(data[131:139]))
		if math.Abs(scale-.000001) > 1e-15 {
			t.Fatalf("tiler scale %g, expected exactly one mm to m conversion", scale)
		}
	}
	original, err := os.ReadFile(rawPath)
	if err != nil || !bytes.Equal(raw, original) {
		t.Fatal("raw upload changed", err)
	}
	if _, err := a.resolveMetricScanSourcePath(scan, scan.OwnerID+1); err == nil {
		t.Fatal("foreign owner accepted")
	}
	// A content-addressed canonical input stays immutable even if the raw upload
	// changes mid-compute. Publication must re-resolve the current raw source.
	changed := append([]byte(nil), raw...)
	changed[len(changed)-1] ^= 1
	if err := os.WriteFile(rawPath, changed, 0644); err != nil {
		t.Fatal(err)
	}
	mutateDuringCompute = true
	if _, _, err := a.buildPointcloudPreprocess(context.Background(), scan); err == nil || !strings.Contains(err.Error(), "发生变化") {
		t.Fatal("published output for a replaced millimetre source", err)
	}
	var current DBAssetDerivative
	if err := a.db.First(&current, row.ID).Error; err != nil || current.Version != row.Version {
		t.Fatal("failed build replaced previous derivative", err)
	}
}
