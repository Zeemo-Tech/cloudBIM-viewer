package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestValidateRebarInspectionBindsProductionProvenanceAndEvidence(t *testing.T) {
	hash := strings.Repeat("a", 64)
	matrix := []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
	diagnostics := validInspectionDiagnostics(t, hash, matrix, .005)
	if err := validateRebarInspection(json.RawMessage(diagnostics), hash, "control-v4", matrix, .005); err != nil {
		t.Fatalf("valid inspection rejected: %v", err)
	}

	mutate := func(change func(map[string]any)) json.RawMessage {
		var payload map[string]any
		if err := json.Unmarshal([]byte(diagnostics), &payload); err != nil {
			t.Fatal(err)
		}
		change(payload["rebarComparison"].(map[string]any)["inspection"].(map[string]any))
		encoded, _ := json.Marshal(payload)
		return encoded
	}
	bad := []json.RawMessage{
		mutate(func(inspection map[string]any) {
			inspection["provenance"].(map[string]any)["instanceMapHash"] = strings.Repeat("b", 64)
		}),
		mutate(func(inspection map[string]any) { inspection["summary"].(map[string]any)["toleranceM"] = .01 }),
		mutate(func(inspection map[string]any) {
			inspection["bars"].([]any)[0].(map[string]any)["observedSegments"].([]any)[0].(map[string]any)["evidence"] = "inferred"
		}),
	}
	for i, payload := range bad {
		if err := validateRebarInspection(payload, hash, "control-v4", matrix, .005); err == nil {
			t.Fatalf("invalid inspection %d accepted", i)
		}
	}
}

func TestInspectionRecolorSignatureAllowsOnlyToleranceDerivedChanges(t *testing.T) {
	hash := strings.Repeat("a", 64)
	matrix := []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
	before := json.RawMessage(validInspectionDiagnostics(t, hash, matrix, .005))
	var payload map[string]any
	_ = json.Unmarshal(before, &payload)
	inspection := payload["rebarComparison"].(map[string]any)["inspection"].(map[string]any)
	inspection["summary"].(map[string]any)["toleranceM"] = .01
	bar := inspection["bars"].([]any)[0].(map[string]any)
	bar["toleranceM"] = .01
	bar["withinToleranceRatio"] = .5
	after, _ := json.Marshal(payload)
	a, errA := inspectionMeasurementSignature(before)
	b, errB := inspectionMeasurementSignature(after)
	if errA != nil || errB != nil || string(a) != string(b) {
		t.Fatalf("tolerance-only recolor changed measurement signature: %v %v\n%s\n%s", errA, errB, a, b)
	}
	bar["observedSegments"].([]any)[0].(map[string]any)["pointCount"] = float64(99)
	after, _ = json.Marshal(payload)
	b, _ = inspectionMeasurementSignature(after)
	if string(a) == string(b) {
		t.Fatal("geometry/measurement mutation was not detected")
	}
}
