package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func metricLASFixture(t *testing.T, minor byte, units string) []byte {
	t.Helper()
	headerSize := map[byte]int{2: 227, 3: 235, 4: 375}[minor]
	header := make([]byte, headerSize)
	copy(header, "LASF")
	header[24], header[25], header[104] = 1, minor, 2
	binary.LittleEndian.PutUint16(header[94:96], uint16(headerSize))
	binary.LittleEndian.PutUint16(header[105:107], 26)
	binary.LittleEndian.PutUint32(header[107:111], 2)
	if minor == 4 {
		binary.LittleEndian.PutUint64(header[247:255], 2)
	}
	values := []float64{.001, .002, .003, 1200, -2400, 3600, 1200.1, 1199.9, -2399.6, -2400.4, 3600.9, 3599.1}
	for i, value := range values {
		binary.LittleEndian.PutUint64(header[131+8*i:], math.Float64bits(value))
	}
	vlr := func(user string, id uint16, payload []byte) []byte {
		v := make([]byte, 54)
		copy(v[2:18], user)
		binary.LittleEndian.PutUint16(v[18:20], id)
		binary.LittleEndian.PutUint16(v[20:22], uint16(len(payload)))
		copy(v[22:54], "fixture description")
		return append(v, payload...)
	}
	count := uint32(1)
	result := append(header, vlr("Other_vendor", 42, []byte("unrelated payload\x00"))...)
	if units != "" {
		symbol, factor := units, 0.0
		if units == "millimeters" {
			symbol, factor = "mm", .001
		} else if units == "meters" {
			symbol, factor = "m", 1
		}
		payload, err := json.Marshal(map[string]any{"units": units, "unit_symbol": symbol, "meters_per_unit": factor, "provenance": "original scanner", "source_point_count": 2})
		if err != nil {
			t.Fatal(err)
		}
		result = append(result, vlr("Lumos_units", 1, payload)...)
		count++
	}
	result = append(result, []byte("padding!")...)
	binary.LittleEndian.PutUint32(result[100:104], count)
	binary.LittleEndian.PutUint32(result[96:100], uint32(len(result)))
	points := make([]byte, 52)
	for i := range points {
		points[i] = byte(3*i + 7)
	}
	for i, coordinates := range [][3]int32{{100, 200, 300}, {-100, -200, -300}} {
		for axis, value := range coordinates {
			binary.LittleEndian.PutUint32(points[i*26+axis*4:], uint32(value))
		}
	}
	return append(result, points...)
}

func writeMetricLASFixture(t *testing.T, dir string, data []byte) string {
	t.Helper()
	path := filepath.Join(dir, "source.las")
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatal(err)
	}
	return path
}

func readMetricLASFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestPrepareMetricPointcloudSourcePreservesSourceAndPointRecords(t *testing.T) {
	dir := t.TempDir()
	original := metricLASFixture(t, 2, "millimeters")
	source := writeMetricLASFixture(t, dir, original)
	sourceInfo, _ := os.Stat(source)
	output, err := prepareMetricPointcloudSource(source, dir)
	if err != nil {
		t.Fatal(err)
	}
	if output == source {
		t.Fatal("explicit millimetres were not normalized")
	}
	converted := readMetricLASFile(t, output)
	if !bytes.Equal(original, readMetricLASFile(t, source)) {
		t.Fatal("source bytes changed")
	}
	currentSourceInfo, _ := os.Stat(source)
	outputInfo, _ := os.Stat(output)
	if !sourceInfo.ModTime().Equal(currentSourceInfo.ModTime()) || os.SameFile(currentSourceInfo, outputInfo) {
		t.Fatal("source was modified or hardlinked")
	}
	if outputInfo.Mode().Perm()&0222 != 0 {
		t.Fatal("derived source should be read-only")
	}
	oldOffset := binary.LittleEndian.Uint32(original[96:100])
	newOffset := binary.LittleEndian.Uint32(converted[96:100])
	if !bytes.Equal(original[oldOffset:], converted[newOffset:]) {
		t.Fatal("point records changed (including RGB and other attributes)")
	}
	for offset := 131; offset < 227; offset += 8 {
		before := math.Float64frombits(binary.LittleEndian.Uint64(original[offset:]))
		after := math.Float64frombits(binary.LittleEndian.Uint64(converted[offset:]))
		if after != before*.001 {
			t.Fatalf("header value at %d: %g -> %g", offset, before, after)
		}
	}
	for i := 0; i < 2; i++ {
		for axis := 0; axis < 3; axis++ {
			coordinate := float64(int32(binary.LittleEndian.Uint32(converted[int(newOffset)+i*26+axis*4:])))
			scale := math.Float64frombits(binary.LittleEndian.Uint64(converted[131+axis*8:]))
			offset := math.Float64frombits(binary.LittleEndian.Uint64(converted[155+axis*8:]))
			originalScale := math.Float64frombits(binary.LittleEndian.Uint64(original[131+axis*8:]))
			originalOffset := math.Float64frombits(binary.LittleEndian.Uint64(original[155+axis*8:]))
			if math.Abs((coordinate*scale+offset)-(coordinate*originalScale+originalOffset)*.001) > 1e-12 {
				t.Fatal("reconstructed point did not convert to metres")
			}
		}
	}
	unrelatedEnd := 227 + 54 + int(binary.LittleEndian.Uint16(original[227+20:]))
	if !bytes.Equal(original[227:unrelatedEnd], converted[227:unrelatedEnd]) || !bytes.Equal(converted[newOffset-8:newOffset], []byte("padding!")) {
		t.Fatal("unrelated VLR or prefix padding changed")
	}
	unitLength := int(binary.LittleEndian.Uint16(converted[unrelatedEnd+20:]))
	var metadata map[string]any
	if err := json.Unmarshal(converted[unrelatedEnd+54:unrelatedEnd+54+unitLength], &metadata); err != nil {
		t.Fatal(err)
	}
	if metadata["units"] != "meters" || metadata["unit_symbol"] != "m" || metadata["meters_per_unit"] != float64(1) || metadata["provenance"] != "original scanner" {
		t.Fatalf("wrong unit metadata: %v", metadata)
	}
	originalMetadata := metadata["cloudbim_original_unit_metadata"].(map[string]any)
	if originalMetadata["units"] != "millimeters" || originalMetadata["meters_per_unit"] != .001 {
		t.Fatal("original units not retained")
	}
	second, err := prepareMetricPointcloudSource(output, dir)
	if err != nil || second != output || !bytes.Equal(converted, readMetricLASFile(t, second)) {
		t.Fatalf("normalization was not idempotent: %q %v", second, err)
	}
}

func TestPrepareMetricPointcloudSourceLegacyAndMetreInputs(t *testing.T) {
	for _, units := range []string{"", "meters", "unknown"} {
		t.Run(units, func(t *testing.T) {
			dir := t.TempDir()
			data := metricLASFixture(t, 2, units)
			source := writeMetricLASFixture(t, dir, data)
			output, err := prepareMetricPointcloudSource(source, dir)
			if err != nil || output != source || !bytes.Equal(data, readMetricLASFile(t, source)) {
				t.Fatalf("legacy/metre input changed: %q %v", output, err)
			}
		})
	}
	for _, data := range [][]byte{nil, []byte("LAS"), []byte("dummy pointcloud fixture")} {
		dir := t.TempDir()
		source := writeMetricLASFixture(t, dir, data)
		if output, err := prepareMetricPointcloudSource(source, dir); err != nil || output != source {
			t.Fatalf("non-LAS compatibility: %q %v", output, err)
		}
	}
}

func TestPrepareMetricPointcloudSourceCacheAndConcurrency(t *testing.T) {
	dir := t.TempDir()
	data := metricLASFixture(t, 2, "millimeters")
	source := writeMetricLASFixture(t, dir, data)
	const workers = 12
	outputs := make([]string, workers)
	errors := make([]error, workers)
	var wg sync.WaitGroup
	for i := range outputs {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			outputs[i], errors[i] = prepareMetricPointcloudSource(source, dir)
		}(i)
	}
	wg.Wait()
	for i := range outputs {
		if errors[i] != nil || outputs[i] != outputs[0] {
			t.Fatalf("concurrent publication: %q %v", outputs[i], errors[i])
		}
	}
	cachedBytes := readMetricLASFile(t, outputs[0])
	stamp := time.Unix(1000000000, 0)
	if err := os.Chtimes(outputs[0], stamp, stamp); err != nil {
		t.Fatal(err)
	}
	reused, err := prepareMetricPointcloudSource(source, dir)
	if err != nil || reused != outputs[0] {
		t.Fatalf("did not reuse cache: %q %v", reused, err)
	}
	info, _ := os.Stat(reused)
	if !info.ModTime().Equal(stamp) {
		t.Fatal("cache reuse changed mtime")
	}
	data[len(data)-1] ^= 0x7f // Same length, different content.
	writeMetricLASFixture(t, dir, data)
	changed, err := prepareMetricPointcloudSource(source, dir)
	if err != nil || changed == reused {
		t.Fatalf("changed content reused stale artifact: %q %v", changed, err)
	}
	if !bytes.Equal(cachedBytes, readMetricLASFile(t, reused)) {
		t.Fatal("new content overwrote prior artifact")
	}
	temps, _ := filepath.Glob(filepath.Join(dir, ".source-meters-*"))
	if len(temps) != 0 {
		t.Fatal("normalization leaked temporary files")
	}
}

func TestPrepareMetricPointcloudSourcePreservesEVLR(t *testing.T) {
	dir := t.TempDir()
	data := metricLASFixture(t, 4, "millimeters")
	evlrOffset := len(data)
	evlr := make([]byte, 60)
	copy(evlr[2:18], "Other_vendor")
	binary.LittleEndian.PutUint16(evlr[18:20], 99)
	payload := []byte("extended payload with metadata")
	binary.LittleEndian.PutUint64(evlr[20:28], uint64(len(payload)))
	data = append(data, append(evlr, payload...)...)
	binary.LittleEndian.PutUint64(data[235:243], uint64(evlrOffset))
	binary.LittleEndian.PutUint32(data[243:247], 1)
	source := writeMetricLASFixture(t, dir, data)
	output, err := prepareMetricPointcloudSource(source, dir)
	if err != nil {
		t.Fatal(err)
	}
	converted := readMetricLASFile(t, output)
	newOffset := binary.LittleEndian.Uint64(converted[235:243])
	if !bytes.Equal(data[evlrOffset:], converted[newOffset:]) || int64(newOffset)-int64(evlrOffset) != int64(len(converted)-len(data)) {
		t.Fatal("EVLR contents or absolute offset not preserved")
	}
}

func TestPrepareMetricPointcloudSourceRejectsUnsafeConversion(t *testing.T) {
	cases := map[string]func([]byte) []byte{
		"compressed":            func(b []byte) []byte { b[104] |= 0x80; return b },
		"waveform format":       func(b []byte) []byte { b[104] = 4; return b },
		"unsupported version":   func(b []byte) []byte { b[25] = 1; return b },
		"truncated points":      func(b []byte) []byte { return b[:len(b)-1] },
		"invalid scale":         func(b []byte) []byte { binary.LittleEndian.PutUint64(b[131:], math.Float64bits(math.NaN())); return b },
		"invalid record length": func(b []byte) []byte { binary.LittleEndian.PutUint16(b[105:], 0); return b },
		"conflicting factor": func(b []byte) []byte {
			return bytes.Replace(b, []byte(`"meters_per_unit":0.001`), []byte(`"meters_per_unit":0.002`), 1)
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			data := mutate(metricLASFixture(t, 2, "millimeters"))
			source := writeMetricLASFixture(t, dir, data)
			if output, err := prepareMetricPointcloudSource(source, dir); err == nil || output != "" {
				t.Fatalf("unsafe conversion accepted: %q %v", output, err)
			}
			if !bytes.Equal(data, readMetricLASFile(t, source)) {
				t.Fatal("failure mutated source")
			}
		})
	}
}
