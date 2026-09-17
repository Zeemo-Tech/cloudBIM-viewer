package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
)

const (
	rebarInspectionSchema = "rebar-inspection-v1"
	rebarInspectionMethod = "control-net-real-point-radial-correspondence-v1"
)

type rebarInspection struct {
	Schema          string                    `json:"schema"`
	CoordinateFrame string                    `json:"coordinateFrame"`
	LengthUnit      string                    `json:"lengthUnit"`
	Method          string                    `json:"method"`
	Provenance      rebarInspectionProvenance `json:"provenance"`
	Bars            []rebarInspectionBar      `json:"bars"`
	Spacing         []rebarInspectionSpacing  `json:"spacing"`
	Summary         rebarInspectionSummary    `json:"summary"`
}

type rebarInspectionProvenance struct {
	InstanceMapHash            string    `json:"instanceMapHash"`
	ControlNetAlgorithmVersion string    `json:"controlNetAlgorithmVersion"`
	AlignmentMatrix            []float64 `json:"alignmentMatrix"`
}

type rebarInspectionBar struct {
	DesignBarID          string                           `json:"designBarId"`
	IFCGlobalID          string                           `json:"ifcGlobalId"`
	Status               string                           `json:"status"`
	UnitIDs              []string                         `json:"unitIds"`
	ObservedSegments     []rebarInspectionObservedSegment `json:"observedSegments"`
	KnownVertexCount     int                              `json:"knownVertexCount"`
	UnknownVertexCount   int                              `json:"unknownVertexCount"`
	ToleranceM           float64                          `json:"toleranceM"`
	WithinToleranceRatio *float64                         `json:"withinToleranceRatio"`
	Quality              rebarInspectionBarQuality        `json:"quality"`
}

type rebarInspectionObservedSegment struct {
	DesignUnitID       string      `json:"designUnitId"`
	Kind               string      `json:"kind"`
	Evidence           string      `json:"evidence"`
	Centerline         [][]float64 `json:"centerline"`
	SupportedIntervals [][]float64 `json:"supportedIntervalsM"`
	PointCount         int         `json:"pointCount"`
	RadiusM            float64     `json:"radiusM"`
	RadiusSource       string      `json:"radiusSource"`
}

type rebarInspectionBarQuality struct {
	SupportedUnitCount int    `json:"supportedUnitCount"`
	DesignUnitCount    int    `json:"designUnitCount"`
	Reason             string `json:"reason"`
}

type rebarInspectionSpacing struct {
	PairID                string                         `json:"pairId"`
	DesignBarIDs          []string                       `json:"designBarIds"`
	IFCGlobalIDs          []string                       `json:"ifcGlobalIds"`
	DesignUnitIDs         []string                       `json:"designUnitIds"`
	FamilyID              string                         `json:"familyId"`
	LayerID               string                         `json:"layerId"`
	DesignDirection       []float64                      `json:"designDirection"`
	SpacingDirection      []float64                      `json:"spacingDirection"`
	DesignCenterDistanceM float64                        `json:"designCenterDistanceM"`
	RadiusSource          string                         `json:"radiusSource"`
	DesignRadiiM          []float64                      `json:"designRadiiM"`
	Samples               []rebarInspectionSpacingSample `json:"samples"`
	ActualCenterDistanceM *float64                       `json:"actualCenterDistanceM"`
	SignedDifferenceM     *float64                       `json:"signedDifferenceM"`
	NetClearanceM         *float64                       `json:"netClearanceM"`
	ToleranceM            float64                        `json:"toleranceM"`
	WithinTolerance       *bool                          `json:"withinTolerance"`
	Coverage              rebarInspectionSpacingCoverage `json:"coverage"`
}

type rebarInspectionSpacingSample struct {
	StationM              float64   `json:"stationM"`
	Status                string    `json:"status"`
	CenterA               []float64 `json:"centerA"`
	CenterB               []float64 `json:"centerB"`
	ActualCenterDistanceM *float64  `json:"actualCenterDistanceM"`
	SignedDifferenceM     *float64  `json:"signedDifferenceM"`
	NetClearanceM         *float64  `json:"netClearanceM"`
	WithinTolerance       *bool     `json:"withinTolerance"`
}

type rebarInspectionSpacingCoverage struct {
	Status         string  `json:"status"`
	SampleCount    int     `json:"sampleCount"`
	SharedSpanM    float64 `json:"sharedSpanM"`
	SupportedSpanM float64 `json:"supportedSpanM"`
	Reason         string  `json:"reason"`
}

type rebarInspectionSummary struct {
	BarCount                    int     `json:"barCount"`
	SupportedBarCount           int     `json:"supportedBarCount"`
	PartialBarCount             int     `json:"partialBarCount"`
	UnavailableBarCount         int     `json:"unavailableBarCount"`
	SpacingPairCount            int     `json:"spacingPairCount"`
	SupportedSpacingPairCount   int     `json:"supportedSpacingPairCount"`
	PartialSpacingPairCount     int     `json:"partialSpacingPairCount"`
	UnavailableSpacingPairCount int     `json:"unavailableSpacingPairCount"`
	ToleranceM                  float64 `json:"toleranceM"`
	ToleranceBasis              string  `json:"toleranceBasis"`
}

func rebarInspectionJSON(diagnostics json.RawMessage) json.RawMessage {
	var data struct {
		Comparison struct {
			Inspection json.RawMessage `json:"inspection"`
		} `json:"rebarComparison"`
	}
	_ = json.Unmarshal(diagnostics, &data)
	return data.Comparison.Inspection
}

func finiteNumber(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }

func sameNumber(a, b float64) bool {
	scale := math.Max(1, math.Max(math.Abs(a), math.Abs(b)))
	return math.Abs(a-b) <= 1e-9*scale
}

func validVector(v []float64, nonzero bool) bool {
	if len(v) != 3 {
		return false
	}
	norm2 := 0.0
	for _, n := range v {
		if !finiteNumber(n) {
			return false
		}
		norm2 += n * n
	}
	return !nonzero || norm2 > 0
}

func validMatrix16(actual, expected []float64) bool {
	if len(actual) != 16 || len(expected) != 16 {
		return false
	}
	for i := range actual {
		if !finiteNumber(actual[i]) || !finiteNumber(expected[i]) || !sameNumber(actual[i], expected[i]) {
			return false
		}
	}
	return true
}

func validateRebarInspection(diagnostics json.RawMessage, expectedHash, controlVersion string, alignmentMatrix []float64, tolerance float64) error {
	var comparison rebarComparison
	if json.Unmarshal(rebarComparisonJSON(diagnostics), &comparison) != nil || comparison.AlgorithmVersion != rebarC2MAlgorithm {
		return errors.New("逐钢筋结果未声明当前检验算法版本")
	}
	var inspection rebarInspection
	if json.Unmarshal(rebarInspectionJSON(diagnostics), &inspection) != nil || inspection.Schema != rebarInspectionSchema || inspection.CoordinateFrame != "bim" || inspection.LengthUnit != "m" || inspection.Method != rebarInspectionMethod {
		return errors.New("逐钢筋结果缺少有效的控制网检验报告")
	}
	if !hex64(inspection.Provenance.InstanceMapHash) || inspection.Provenance.InstanceMapHash != expectedHash || comparison.InstanceMapHash != expectedHash ||
		strings.TrimSpace(controlVersion) == "" || inspection.Provenance.ControlNetAlgorithmVersion != controlVersion || !validMatrix16(inspection.Provenance.AlignmentMatrix, alignmentMatrix) {
		return errors.New("检验报告与实例映射、控制网或配准矩阵不一致")
	}
	if !finiteNumber(tolerance) || tolerance <= 0 || !sameNumber(inspection.Summary.ToleranceM, tolerance) || inspection.Summary.ToleranceBasis != "request.toleranceLimit" {
		return errors.New("检验报告的容差来源与请求不一致")
	}
	comparisonBars := make(map[string]rebarComparisonBar, len(comparison.Bars))
	for _, bar := range comparison.Bars {
		comparisonBars[bar.DesignBarID+"\x00"+bar.IFCGlobalID] = bar
	}
	seenBars := make(map[string]bool, len(inspection.Bars))
	barStatus := map[string]int{"supported": 0, "partial": 0, "missing": 0, "review": 0, "unavailable": 0}
	for _, bar := range inspection.Bars {
		key := bar.DesignBarID + "\x00" + bar.IFCGlobalID
		comp, exists := comparisonBars[key]
		if !exists || seenBars[key] || barStatus[bar.Status] < 0 || strings.TrimSpace(bar.DesignBarID) == "" || strings.TrimSpace(bar.IFCGlobalID) == "" {
			return errors.New("检验钢筋身份、状态或数量无效")
		}
		if _, validStatus := barStatus[bar.Status]; !validStatus {
			return errors.New("检验钢筋状态无效")
		}
		barStatus[bar.Status]++
		seenBars[key] = true
		if bar.KnownVertexCount != comp.KnownCount || bar.UnknownVertexCount != comp.UnknownCount || bar.KnownVertexCount < 0 || bar.UnknownVertexCount < 0 || !sameNumber(bar.ToleranceM, tolerance) {
			return errors.New("检验钢筋覆盖或容差与逐钢筋结果不一致")
		}
		if bar.WithinToleranceRatio != nil && (!finiteNumber(*bar.WithinToleranceRatio) || *bar.WithinToleranceRatio < 0 || *bar.WithinToleranceRatio > 1) {
			return errors.New("检验钢筋容差比例无效")
		}
		unitSet := make(map[string]bool, len(bar.UnitIDs))
		for _, id := range bar.UnitIDs {
			if strings.TrimSpace(id) == "" || unitSet[id] {
				return errors.New("检验钢筋设计单元无效或重复")
			}
			unitSet[id] = true
		}
		if bar.Quality.DesignUnitCount != len(bar.UnitIDs) || bar.Quality.SupportedUnitCount < 0 || bar.Quality.SupportedUnitCount > bar.Quality.DesignUnitCount ||
			!oneOf(bar.Quality.Reason, "supported", "partial-control-net", "no-trusted-control-geometry", "review-required") {
			return errors.New("检验钢筋质量摘要无效")
		}
		for _, segment := range bar.ObservedSegments {
			if !unitSet[segment.DesignUnitID] || !oneOf(segment.Kind, "body", "curve") || !oneOf(segment.Evidence, "fitted-control-net", "local-control-support") || segment.PointCount < 0 ||
				!finiteNumber(segment.RadiusM) || segment.RadiusM <= 0 || segment.RadiusSource != "design-prior" || len(segment.Centerline) < 2 || len(segment.SupportedIntervals) == 0 {
				return errors.New("检验报告包含无效或非拟合控制网证据")
			}
			for _, point := range segment.Centerline {
				if !validVector(point, false) {
					return errors.New("检验报告控制线坐标无效")
				}
			}
			for _, interval := range segment.SupportedIntervals {
				if len(interval) != 2 || !finiteNumber(interval[0]) || !finiteNumber(interval[1]) || interval[0] < 0 || interval[1] < interval[0] {
					return errors.New("检验报告控制线支持区间无效")
				}
			}
		}
	}
	if len(seenBars) != len(comparisonBars) {
		return errors.New("检验报告未覆盖全部设计钢筋")
	}
	spacingStatus := map[string]int{"supported": 0, "partial": 0, "unavailable": 0}
	seenPairs := make(map[string]bool, len(inspection.Spacing))
	for _, spacing := range inspection.Spacing {
		if err := validateInspectionSpacing(spacing, seenBars, tolerance); err != nil {
			return err
		}
		if seenPairs[spacing.PairID] {
			return errors.New("检验报告包含重复间距对")
		}
		seenPairs[spacing.PairID] = true
		spacingStatus[spacing.Coverage.Status]++
	}
	s := inspection.Summary
	if s.BarCount != len(inspection.Bars) || s.BarCount != len(comparison.Bars) || s.SupportedBarCount != barStatus["supported"] || s.PartialBarCount != barStatus["partial"] ||
		s.UnavailableBarCount != barStatus["missing"]+barStatus["review"]+barStatus["unavailable"] || s.SpacingPairCount != len(inspection.Spacing) ||
		s.SupportedSpacingPairCount != spacingStatus["supported"] || s.PartialSpacingPairCount != spacingStatus["partial"] || s.UnavailableSpacingPairCount != spacingStatus["unavailable"] {
		return errors.New("检验报告汇总与明细不一致")
	}
	return nil
}

func validateInspectionSpacing(spacing rebarInspectionSpacing, bars map[string]bool, tolerance float64) error {
	if len(spacing.DesignBarIDs) != 2 || len(spacing.IFCGlobalIDs) != 2 || len(spacing.DesignUnitIDs) != 2 || len(spacing.DesignRadiiM) != 2 ||
		strings.TrimSpace(spacing.FamilyID) == "" || strings.TrimSpace(spacing.LayerID) == "" || spacing.RadiusSource != "design-prior" ||
		!validVector(spacing.DesignDirection, true) || !validVector(spacing.SpacingDirection, true) || !finiteNumber(spacing.DesignCenterDistanceM) || spacing.DesignCenterDistanceM <= 0 || !sameNumber(spacing.ToleranceM, tolerance) {
		return errors.New("钢筋间距对的设计来源或方向无效")
	}
	units := append([]string(nil), spacing.DesignUnitIDs...)
	sort.Strings(units)
	if strings.TrimSpace(units[0]) == "" || units[0] == units[1] || spacing.PairID != units[0]+"|"+units[1] {
		return errors.New("钢筋间距对标识无效")
	}
	for i := 0; i < 2; i++ {
		if !bars[spacing.DesignBarIDs[i]+"\x00"+spacing.IFCGlobalIDs[i]] || !finiteNumber(spacing.DesignRadiiM[i]) || spacing.DesignRadiiM[i] <= 0 {
			return errors.New("钢筋间距对引用了无效钢筋或半径")
		}
	}
	if !oneOf(spacing.Coverage.Status, "supported", "partial", "unavailable") || !oneOf(spacing.Coverage.Reason, "supported", "sparse-observation", "unmatched-unit", "ambiguous-family-or-layer") ||
		!finiteNumber(spacing.Coverage.SharedSpanM) || spacing.Coverage.SharedSpanM < 0 || !finiteNumber(spacing.Coverage.SupportedSpanM) || spacing.Coverage.SupportedSpanM < 0 || spacing.Coverage.SupportedSpanM > spacing.Coverage.SharedSpanM {
		return errors.New("钢筋间距覆盖摘要无效")
	}
	supportedSamples := 0
	for _, sample := range spacing.Samples {
		if !finiteNumber(sample.StationM) || !oneOf(sample.Status, "supported", "unknown") {
			return errors.New("钢筋间距样本的距离、净距或容差结论矛盾")
		}
		if sample.Status == "unknown" {
			if sample.CenterA != nil || sample.CenterB != nil || sample.ActualCenterDistanceM != nil || sample.SignedDifferenceM != nil || sample.NetClearanceM != nil || sample.WithinTolerance != nil {
				return errors.New("未知钢筋间距样本不能声明测量值")
			}
			continue
		}
		supportedSamples++
		if !validVector(sample.CenterA, false) || !validVector(sample.CenterB, false) || sample.ActualCenterDistanceM == nil || sample.SignedDifferenceM == nil || sample.NetClearanceM == nil || sample.WithinTolerance == nil ||
			!finiteNumber(*sample.ActualCenterDistanceM) || *sample.ActualCenterDistanceM < 0 || !finiteNumber(*sample.SignedDifferenceM) || !finiteNumber(*sample.NetClearanceM) ||
			!sameNumber(*sample.SignedDifferenceM, *sample.ActualCenterDistanceM-spacing.DesignCenterDistanceM) || !sameNumber(*sample.NetClearanceM, *sample.ActualCenterDistanceM-spacing.DesignRadiiM[0]-spacing.DesignRadiiM[1]) ||
			*sample.WithinTolerance != (math.Abs(*sample.SignedDifferenceM) <= tolerance) {
			return errors.New("钢筋间距样本的距离、净距或容差结论矛盾")
		}
	}
	if spacing.Coverage.SampleCount != supportedSamples || spacing.Coverage.Status == "supported" && supportedSamples != len(spacing.Samples) ||
		spacing.Coverage.Status == "partial" && (supportedSamples == 0 || supportedSamples == len(spacing.Samples)) || spacing.Coverage.Status == "unavailable" && supportedSamples != 0 {
		return errors.New("钢筋间距状态与样本覆盖矛盾")
	}
	if spacing.Coverage.Status == "unavailable" {
		if spacing.ActualCenterDistanceM != nil || spacing.SignedDifferenceM != nil || spacing.NetClearanceM != nil || spacing.WithinTolerance != nil {
			return errors.New("不可用间距不能声明测量值")
		}
	} else {
		if spacing.ActualCenterDistanceM == nil || spacing.SignedDifferenceM == nil || spacing.NetClearanceM == nil || spacing.WithinTolerance == nil ||
			!finiteNumber(*spacing.ActualCenterDistanceM) || *spacing.ActualCenterDistanceM < 0 || !finiteNumber(*spacing.SignedDifferenceM) || !finiteNumber(*spacing.NetClearanceM) ||
			!sameNumber(*spacing.SignedDifferenceM, *spacing.ActualCenterDistanceM-spacing.DesignCenterDistanceM) || !sameNumber(*spacing.NetClearanceM, *spacing.ActualCenterDistanceM-spacing.DesignRadiiM[0]-spacing.DesignRadiiM[1]) ||
			*spacing.WithinTolerance != (math.Abs(*spacing.SignedDifferenceM) <= tolerance) {
			return errors.New("钢筋间距汇总的距离、净距或容差结论矛盾")
		}
	}
	return nil
}

func oneOf(value string, allowed ...string) bool {
	for _, item := range allowed {
		if value == item {
			return true
		}
	}
	return false
}

func inspectionMeasurementSignature(diagnostics json.RawMessage) ([]byte, error) {
	var inspection map[string]any
	if json.Unmarshal(rebarInspectionJSON(diagnostics), &inspection) != nil || inspection == nil {
		return nil, errors.New("检验报告不存在")
	}
	if bars, ok := inspection["bars"].([]any); ok {
		for _, raw := range bars {
			if bar, ok := raw.(map[string]any); ok {
				delete(bar, "toleranceM")
				delete(bar, "withinToleranceRatio")
			}
		}
	}
	if rows, ok := inspection["spacing"].([]any); ok {
		for _, raw := range rows {
			if spacing, ok := raw.(map[string]any); ok {
				delete(spacing, "toleranceM")
				delete(spacing, "withinTolerance")
				if samples, ok := spacing["samples"].([]any); ok {
					for _, sampleRaw := range samples {
						if sample, ok := sampleRaw.(map[string]any); ok {
							delete(sample, "withinTolerance")
						}
					}
				}
			}
		}
	}
	if summary, ok := inspection["summary"].(map[string]any); ok {
		delete(summary, "toleranceM")
	}
	encoded, err := json.Marshal(inspection)
	if err != nil {
		return nil, fmt.Errorf("编码检验报告失败: %w", err)
	}
	return encoded, nil
}
