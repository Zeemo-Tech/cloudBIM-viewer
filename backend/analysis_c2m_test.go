package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAnalysisC2MFingerprint(t *testing.T) {
	h := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	a, _ := AnalysisC2MFingerprint(h, h, make([]float64, 16), map[string]any{"a": 1})
	b, _ := AnalysisC2MFingerprint(h, h, make([]float64, 16), map[string]any{"a": 1})
	if a != b {
		t.Fatal("unstable")
	}
	c, _ := AnalysisC2MFingerprint(h, h, append([]float64{1}, make([]float64, 15)...), map[string]any{"a": 1})
	if a == c {
		t.Fatal("transform omitted")
	}
	d, _ := AnalysisC2MFingerprint(h, h, make([]float64, 16), map[string]any{"a": 1, "implementationVersion": "2"})
	if a == d {
		t.Fatal("algorithm implementation version omitted")
	}
}
func TestAnalysisC2MPublish(t *testing.T) {
	r := t.TempDir()
	s := filepath.Join(r, "stage")
	f := filepath.Join(r, "final")
	if e := os.Mkdir(s, 0750); e != nil {
		t.Fatal(e)
	}
	if e := PublishAnalysisC2M(s, f); e != nil {
		t.Fatal(e)
	}
	if _, e := os.Stat(f); e != nil {
		t.Fatal(e)
	}
}

func TestAnalysisC2MResourceMustBeDeclared(t *testing.T) {
	root := t.TempDir()
	manifest := AnalysisC2MManifest{ContentHash: strings.Repeat("a", 64), Files: map[string]AnalysisC2MFile{"distances/tile.f32": {SHA256: strings.Repeat("b", 64), ByteLength: 4}}}
	payload, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), payload, 0600); err != nil {
		t.Fatal(err)
	}
	metadata, err := canonicalJSON(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if !analysisC2MResourceDeclared(root, "manifest.json", manifest.ContentHash, metadata) || !analysisC2MResourceDeclared(root, "distances/tile.f32", manifest.ContentHash, metadata) || analysisC2MResourceDeclared(root, "extra.bin", manifest.ContentHash, metadata) {
		t.Fatal("analysis-c2m resource registry was not enforced")
	}
}

func TestValidateAnalysisC2MManifestBindsPayloadAndStatistics(t *testing.T) {
	root := t.TempDir()
	distancePath := "distances/tile-000000.f32"
	if err := os.MkdirAll(filepath.Join(root, "distances"), 0750); err != nil {
		t.Fatal(err)
	}
	payload := make([]byte, 8)
	binary.LittleEndian.PutUint32(payload[0:4], math.Float32bits(float32(0.125)))
	binary.LittleEndian.PutUint32(payload[4:8], math.Float32bits(float32(math.NaN())))
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(distancePath)), payload, 0600); err != nil {
		t.Fatal(err)
	}
	payloadHash := sha256.Sum256(payload)
	payloadHashText := hex.EncodeToString(payloadHash[:])
	aggregate := sha256.New()
	_, _ = aggregate.Write([]byte(distancePath))
	_, _ = aggregate.Write([]byte{0})
	_, _ = aggregate.Write([]byte(payloadHashText))
	value, zero := 0.125, 0.0
	stats := AnalysisC2MStats{KnownCount: 1, UnknownCount: 1, Min: &value, Max: &value, Mean: &value, Std: &zero}
	manifest := AnalysisC2MManifest{
		Schema:      "analysis-c2m-result-v1",
		Immutable:   true,
		ContentHash: hex.EncodeToString(aggregate.Sum(nil)),
		Transform:   make([]float64, 16),
		Files: map[string]AnalysisC2MFile{
			distancePath: {SHA256: payloadHashText, ByteLength: int64(len(payload))},
		},
		Tiles:  []AnalysisC2MTile{{TileID: "tile-000000", IFCGlobalID: "G1", PartID: "G1:part", PositionHash: strings.Repeat("a", 64), VertexCount: 2, DistancePath: distancePath, SHA256: payloadHashText, ByteLength: int64(len(payload)), Stats: stats}},
		Global: stats,
	}
	manifest.UnknownEncoding.Type = "ieee754-float32"
	manifest.UnknownEncoding.Value = "NaN"
	manifest.UnknownEncoding.ByteOrder = "little-endian"
	manifest.InputAnalysisMesh.ContentHash = strings.Repeat("b", 64)
	manifest.InputAnalysisMesh.ArtifactVersion = "analysis-mesh-artifact-v1"
	manifest.InputAnalysisMesh.ModelFrame.SourceBounds.Min = []float64{-1, -1, -1}
	manifest.InputAnalysisMesh.ModelFrame.SourceBounds.Max = []float64{1, 1, 1}
	manifest.InputAnalysisMesh.ModelFrame.NormalizationCenter = []float64{0, 0, 0}
	manifest.Algorithm.ID = "c2m-tile-nearest-v1"
	manifest.Algorithm.ImplementationVersion = "1.0.0"
	manifest.Algorithm.ContractVersion = "1"
	manifest.Algorithm.EffectiveParameters = map[string]any{"voxelSize": 0.02}
	manifest.Scan.ContentHash = strings.Repeat("c", 64)
	manifest.Scan.PointsBefore, manifest.Scan.PointsAfter = 2, 2
	manifest.Components = append(manifest.Components, struct {
		IFCGlobalID string           `json:"ifcGlobalId"`
		Stats       AnalysisC2MStats `json:"stats"`
	}{IFCGlobalID: "G1", Stats: stats})
	manifestBytes, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), manifestBytes, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateAnalysisC2MManifest(root, manifest); err != nil {
		t.Fatalf("valid manifest rejected: %v", err)
	}

	shortBytes, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	var shortManifest AnalysisC2MManifest
	if err := json.Unmarshal(shortBytes, &shortManifest); err != nil {
		t.Fatal(err)
	}
	shortPayload := []byte{1, 2, 3}
	shortHash := sha256.Sum256(shortPayload)
	shortHashText := hex.EncodeToString(shortHash[:])
	shortManifest.Files[distancePath] = AnalysisC2MFile{SHA256: shortHashText, ByteLength: int64(len(shortPayload))}
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(distancePath)), shortPayload, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateAnalysisC2MManifest(root, shortManifest); err == nil {
		t.Fatal("short distance payload accepted")
	}

	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(distancePath)), make([]byte, len(payload)), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateAnalysisC2MManifest(root, manifest); err == nil {
		t.Fatal("tampered distance payload accepted")
	}
}
