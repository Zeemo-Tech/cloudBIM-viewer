package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
)

const rebarComparisonSchema = "rebar-comparison-v1"
const rebarC2MAlgorithm = "c2m-rebar-instance-v4"

type rebarComparisonBar struct {
	IFCGlobalID  string    `json:"ifcGlobalId"`
	DesignBarID  string    `json:"designBarId"`
	InstanceIDs  []int     `json:"instanceIds"`
	VertexStart  int       `json:"vertexStart"`
	VertexCount  int       `json:"vertexCount"`
	KnownCount   int       `json:"knownCount"`
	UnknownCount int       `json:"unknownCount"`
	Status       string    `json:"status"`
	Stats        *c2mStats `json:"stats"`
}

// validateC2MEffectiveSettings prevents an upstream fallback from being silently
// represented as a constrained measurement. Legacy (disabled) requests do not
// require the additive effective block.
func validateC2MEffectiveSettings(diagnostics json.RawMessage, req c2mRequest) error {
	var payload struct {
		Comparison struct {
			Effective struct {
				KnnK                    int      `json:"knnK"`
				NormalConstraintEnabled bool     `json:"normalConstraintEnabled"`
				NormalHalfSpaceOnly     bool     `json:"normalHalfSpaceOnly"`
				NormalMaxAngleDeg       float64  `json:"normalMaxAngleDeg"`
				NormalFallbackMode      string   `json:"normalFallbackMode"`
				MaxSearchDistance       *float64 `json:"maxSearchDistance"`
			} `json:"effective"`
		} `json:"rebarComparison"`
	}
	if err := json.Unmarshal(diagnostics, &payload); err != nil {
		return errors.New("C2M 服务未声明有效法向约束设置")
	}
	e := payload.Comparison.Effective
	requestedDistance, returnedDistance := 0.2, 0.2
	if req.MaxSearchDistance != nil {
		requestedDistance = *req.MaxSearchDistance
	}
	if e.MaxSearchDistance != nil {
		returnedDistance = *e.MaxSearchDistance
	}
	if returnedDistance != requestedDistance {
		return errors.New("C2M 服务返回的最大搜索距离与请求不一致")
	}
	if !req.NormalConstraintEnabled {
		return nil
	}
	if !e.NormalConstraintEnabled || e.NormalHalfSpaceOnly || e.KnnK != req.KnnK || e.NormalMaxAngleDeg != req.NormalMaxAngleDeg || e.NormalFallbackMode != req.NormalFallbackMode {
		return fmt.Errorf("C2M 服务返回的有效法向约束设置与请求不一致")
	}
	return nil
}

type rebarComparison struct {
	Schema             string               `json:"schema"`
	AlgorithmVersion   string               `json:"algorithmVersion"`
	Bars               []rebarComparisonBar `json:"bars"`
	KnownVertexCount   int                  `json:"knownVertexCount"`
	UnknownVertexCount int                  `json:"unknownVertexCount"`
	InstanceMapHash    string               `json:"instanceMapHash"`
}

func rebarComparisonJSON(diagnostics json.RawMessage) json.RawMessage {
	var data struct {
		Comparison json.RawMessage `json:"rebarComparison"`
	}
	_ = json.Unmarshal(diagnostics, &data)
	return data.Comparison
}

// The vertex registry is also the report/selection contract: ranges must partition
// the entire steel mesh and an instance cannot be borrowed by a neighbouring bar.
func validateRebarComparison(diagnostics json.RawMessage, vertices int, mapHash string) (int, error) {
	var result rebarComparison
	if json.Unmarshal(rebarComparisonJSON(diagnostics), &result) != nil || result.Schema != rebarComparisonSchema ||
		result.AlgorithmVersion != rebarC2MAlgorithm || !hex64(result.InstanceMapHash) || (mapHash != "" && result.InstanceMapHash != mapHash) || len(result.Bars) == 0 {
		return 0, errors.New("逐钢筋结果缺少有效的实例对应关系")
	}
	seen, instances := map[string]bool{}, map[int]bool{}
	next, known, unknown := 0, 0, 0
	for _, bar := range result.Bars {
		if bar.IFCGlobalID == "" || bar.DesignBarID == "" || seen[bar.IFCGlobalID] || bar.VertexStart != next || bar.VertexCount < 0 ||
			bar.KnownCount < 0 || bar.UnknownCount < 0 || bar.KnownCount+bar.UnknownCount != bar.VertexCount ||
			(bar.Status != "matched" && bar.Status != "missing" && bar.Status != "review") {
			return 0, errors.New("逐钢筋顶点区间或覆盖统计无效")
		}
		if bar.Status != "matched" && bar.KnownCount != 0 {
			return 0, errors.New("待复核或缺测钢筋不能声明有效偏差")
		}
		if !validRebarStatistics(bar.Stats, bar.KnownCount) || (bar.KnownCount > 0 && len(bar.InstanceIDs) == 0) {
			return 0, errors.New("逐钢筋偏差统计与有效覆盖不一致")
		}
		for _, id := range bar.InstanceIDs {
			if id <= 0 || instances[id] {
				return 0, errors.New("钢筋实例归属重复或无效")
			}
			instances[id] = true
		}
		seen[bar.IFCGlobalID] = true
		next += bar.VertexCount
		known += bar.KnownCount
		unknown += bar.UnknownCount
	}
	if next != vertices || known != result.KnownVertexCount || unknown != result.UnknownVertexCount {
		return 0, errors.New("逐钢筋统计与总顶点数不一致")
	}
	return known, nil
}

func validRebarStatistics(stats *c2mStats, known int) bool {
	if known == 0 {
		return stats == nil
	}
	return known > 0 && stats != nil && stats.Min <= stats.Max && stats.Std >= 0 && stats.MeanAbs >= 0 &&
		stats.RMSE >= 0 && stats.P95Abs >= 0 && stats.WithinToleranceRatio >= 0 && stats.WithinToleranceRatio <= 1
}

func (a *app) instanceC2MFingerprint(scan, bim Asset, matrixJSON, scanPath, meshPath string) (string, error) {
	row, denoise, err := a.denoiseRow(scan, bim.ID)
	if err != nil {
		return "", err
	}
	_, mesh, _, err := a.currentAnalysisMesh(bim)
	if err != nil {
		return "", errors.New("逐钢筋对比需要保留 IFC 构件标识的均匀化网格，请重新执行网格均匀化")
	}
	return c2mInputFingerprint(matrixJSON, scanPath, meshPath, bim.RemeshFingerprint,
		rebarC2MAlgorithm, row.Version, denoise.InstancesSHA256, mesh.ContentHash)
}

func (a *app) c2mInstanceMapPath(scan Asset, bimID int64) (string, string, error) {
	row, manifest, err := a.denoiseRow(scan, bimID)
	if err != nil {
		return "", "", err
	}
	path, err := rebarFile(scan.Dir, filepath.Join(row.RelativePath, "instance-map.json"))
	return path, manifest.InstancesSHA256, err
}
