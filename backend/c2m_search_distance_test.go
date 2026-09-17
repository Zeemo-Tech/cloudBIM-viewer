package main

import (
	"encoding/json"
	"math"
	"testing"
)

func TestC2MSearchDistanceDefaultsValidationAndForwarding(t *testing.T) {
	for _, tc := range []struct {
		raw      string
		expected float64
		invalid  bool
	}{
		{`{}`, .2, false}, {`{"maxSearchDistance":0.025}`, .025, false},
		{`{"maxSearchDistance":0.0001}`, .0001, false}, {`{"maxSearchDistance":0.2}`, .2, false},
		{`{"maxSearchDistance":0}`, 0, true}, {`{"maxSearchDistance":-0.01}`, 0, true},
		{`{"maxSearchDistance":0.00001}`, 0, true}, {`{"maxSearchDistance":0.201}`, 0, true},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			var req c2mRequest
			if err := json.Unmarshal([]byte(tc.raw), &req); err != nil {
				t.Fatal(err)
			}
			err := normalizeC2MRequest(&req)
			if tc.invalid {
				if err == nil {
					t.Fatal("invalid distance accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if *req.MaxSearchDistance != tc.expected || c2mServiceParams(req)["max_search_distance"] != tc.expected {
				t.Fatal("distance lost in proxy mapping")
			}
		})
	}
	for _, value := range []float64{math.NaN(), math.Inf(1)} {
		req := c2mRequest{MaxSearchDistance: &value}
		if normalizeC2MRequest(&req) == nil {
			t.Fatal("non-finite distance accepted")
		}
	}
}

func TestC2MRejectsServiceIgnoringCustomSearchDistance(t *testing.T) {
	distance := .025
	for _, enabled := range []bool{false, true} {
		req := c2mRequest{MaxSearchDistance: &distance, NormalConstraintEnabled: enabled}
		if err := normalizeC2MRequest(&req); err != nil {
			t.Fatal(err)
		}
		effective := map[string]any{"knnK": req.KnnK, "normalConstraintEnabled": enabled, "normalHalfSpaceOnly": false, "normalMaxAngleDeg": req.NormalMaxAngleDeg, "normalFallbackMode": req.NormalFallbackMode}
		payload := func() []byte {
			data, _ := json.Marshal(map[string]any{"rebarComparison": map[string]any{"effective": effective}})
			return data
		}
		if validateC2MEffectiveSettings(payload(), req) == nil {
			t.Fatal("missing custom cap was accepted")
		}
		effective["maxSearchDistance"] = .2
		if validateC2MEffectiveSettings(payload(), req) == nil {
			t.Fatal("wrong cap was accepted")
		}
		effective["maxSearchDistance"] = distance
		if err := validateC2MEffectiveSettings(payload(), req); err != nil {
			t.Fatal(err)
		}
	}
}
