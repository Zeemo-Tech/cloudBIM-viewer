package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type AnalysisC2MProvider interface {
	ListAlgorithms(context.Context) ([]map[string]any, error)
	Build(context.Context, AnalysisC2MBuildRequest) (AnalysisC2MManifest, error)
}
type AnalysisC2MBuildRequest struct {
	ScanPath         string         `json:"scanPath"`
	ScanContentHash  string         `json:"scanContentHash"`
	AnalysisMeshPath string         `json:"analysisMeshPath"`
	OutputPath       string         `json:"outputPath"`
	Transform        []float64      `json:"transform"`
	Parameters       map[string]any `json:"parameters"`
	AlgorithmID      string         `json:"algorithmId"`
}
type AnalysisC2MProviderError struct {
	Code       string
	Status     int
	RetryAfter string
	Detail     string
}

func (e *AnalysisC2MProviderError) Error() string { return e.Code + ": " + e.Detail }

type MeshServiceAnalysisC2MProvider struct {
	BaseURL string
	Client  *http.Client
}

func (p MeshServiceAnalysisC2MProvider) client() *http.Client {
	if p.Client != nil {
		return p.Client
	}
	return &http.Client{Timeout: 2 * time.Hour}
}
func (p MeshServiceAnalysisC2MProvider) call(ctx context.Context, method, path string, in, out any) error {
	var b *bytes.Reader
	if in != nil {
		x, e := json.Marshal(in)
		if e != nil {
			return e
		}
		b = bytes.NewReader(x)
	} else {
		b = bytes.NewReader(nil)
	}
	r, e := http.NewRequestWithContext(ctx, method, strings.TrimRight(p.BaseURL, "/")+path, b)
	if e != nil {
		return e
	}
	if in != nil {
		r.Header.Set("Content-Type", "application/json")
	}
	q, e := p.client().Do(r)
	if e != nil {
		return fmt.Errorf("analysis_c2m_provider_failed: %w", e)
	}
	defer q.Body.Close()
	if q.StatusCode < 200 || q.StatusCode >= 300 {
		payload, _ := io.ReadAll(io.LimitReader(q.Body, 64<<10))
		var problem struct {
			ErrorCode string          `json:"errorCode"`
			Message   string          `json:"message"`
			Detail    json.RawMessage `json:"detail"`
		}
		_ = json.Unmarshal(payload, &problem)
		code := strings.TrimSpace(problem.ErrorCode)
		if q.StatusCode == http.StatusTooManyRequests {
			code = "provider_busy"
		} else if code == "" && q.StatusCode >= 400 && q.StatusCode < 500 {
			code = "invalid_parameters"
		} else if code == "" {
			code = "provider_failed"
		}
		detail := strings.TrimSpace(problem.Message)
		if detail == "" {
			detail = strings.Trim(strings.TrimSpace(string(problem.Detail)), `"`)
		}
		if detail == "" {
			detail = strings.TrimSpace(string(payload))
		}
		return &AnalysisC2MProviderError{Code: code, Status: q.StatusCode, RetryAfter: q.Header.Get("Retry-After"), Detail: detail}
	}
	return json.NewDecoder(io.LimitReader(q.Body, 16<<20)).Decode(out)
}
func (p MeshServiceAnalysisC2MProvider) ListAlgorithms(c context.Context) ([]map[string]any, error) {
	var v []map[string]any
	e := p.call(c, "GET", "/analysis-c2m/algorithms", nil, &v)
	return v, e
}
func (p MeshServiceAnalysisC2MProvider) Build(c context.Context, r AnalysisC2MBuildRequest) (AnalysisC2MManifest, error) {
	var v AnalysisC2MManifest
	e := p.call(c, "POST", "/analysis-c2m/build", r, &v)
	return v, e
}
