package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRebarComparisonRejectsBorrowedInstancesAndInvalidCoverage(t *testing.T) {
	base := rebarComparison{Schema: rebarComparisonSchema, InstanceMapHash: strings.Repeat("a", 64), KnownVertexCount: 2, UnknownVertexCount: 4,
		Bars: []rebarComparisonBar{
			{IFCGlobalID: "bar1", DesignBarID: "d1", InstanceIDs: []int{1}, VertexCount: 3, KnownCount: 2, UnknownCount: 1, Status: "matched", Stats: &c2mStats{}},
			{IFCGlobalID: "bar2", DesignBarID: "d2", VertexStart: 3, VertexCount: 3, UnknownCount: 3, Status: "missing"},
		}}
	check := func(value rebarComparison, expectedError bool) {
		data, _ := json.Marshal(map[string]any{"rebarComparison": value})
		known, err := validateRebarComparison(data, 6, base.InstanceMapHash)
		if (err != nil) != expectedError || (!expectedError && known != 2) {
			t.Fatalf("known=%d err=%v", known, err)
		}
	}
	check(base, false)
	for _, mutate := range []func(*rebarComparison){
		func(v *rebarComparison) { v.Bars[1].InstanceIDs = []int{1} },
		func(v *rebarComparison) { v.Bars[1].KnownCount = 1; v.Bars[1].UnknownCount = 2 },
		func(v *rebarComparison) { v.Bars[1].VertexStart = 2 },
		func(v *rebarComparison) { v.InstanceMapHash = strings.Repeat("b", 64) },
	} {
		copy := base
		copy.Bars = append([]rebarComparisonBar{}, base.Bars...)
		mutate(&copy)
		check(copy, true)
	}
}

func TestDenoiseInstanceMapIntegrityAndLegacyResults(t *testing.T) {
	a, scan := denoiseTestApp(t)
	installDenoiseService(t, a, nil)
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	row, manifest, err := a.denoiseRow(scan, 2)
	if err != nil {
		t.Fatal(err)
	}
	legacy := manifest
	legacy.InstanceContract = ""
	if a.denoiseFresh(scan, 2, 10, row, legacy) == nil {
		t.Fatal("legacy instance-free LAS accepted")
	}
	path := filepath.Join(scan.Dir, row.RelativePath, "instance-map.json")
	if err := os.WriteFile(path, []byte("modified map"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := a.resolveC2MScanPath(scan, 2, 10); err == nil {
		t.Fatal("modified mapping accepted")
	}
}

func TestCompletelyUnobservedRebarResultHasNoGlobalDeviationClaim(t *testing.T) {
	data := c2mResultData(DBC2MResult{AlgorithmVersion: rebarC2MAlgorithm, DiagnosticsJSON: `{"rebarComparison":{"knownVertexCount":0}}`})
	if data["stats"] != nil {
		t.Fatalf("unknown result reported numeric stats: %v", data["stats"])
	}
}
