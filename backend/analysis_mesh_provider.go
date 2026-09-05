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

// AnalysisMeshProvider is the owned boundary for the mesh-service analysis-mesh v1 API.
type AnalysisMeshProvider interface {
	ListAlgorithms(context.Context) ([]AnalysisMeshAlgorithmDescriptor, error)
	Build(context.Context, AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error)
}

type AnalysisMeshAlgorithmDescriptor struct {
	ID                    string         `json:"id"`
	Label                 string         `json:"label"`
	ImplementationVersion string         `json:"implementationVersion"`
	ContractVersion       string         `json:"contractVersion"`
	ParameterSchema       map[string]any `json:"parameterSchema"`
	Defaults              map[string]any `json:"defaults"`
	Capabilities          []string       `json:"capabilities"`
}

// AnalysisMeshBuildRequest is the mesh-service analysis-mesh v1 wire contract.
type AnalysisMeshBuildRequest struct {
	ModelPath    string         `json:"modelPath"`
	MetadataPath string         `json:"metadataPath"`
	OutputPath   string         `json:"outputPath"`
	AlgorithmID  string         `json:"algorithmId"`
	Parameters   map[string]any `json:"parameters,omitempty"`
	FaceCap      int            `json:"faceCap,omitempty"`
}

type AnalysisMeshProviderError struct {
	Code       string
	Status     int
	RetryAfter string
	Detail     string
}

func (e *AnalysisMeshProviderError) Error() string {
	if e.Detail != "" {
		return fmt.Sprintf("%s: %s", e.Code, e.Detail)
	}
	return e.Code
}

type MeshServiceAnalysisMeshProvider struct {
	BaseURL string
	Client  *http.Client
}

func (p MeshServiceAnalysisMeshProvider) client() *http.Client {
	if p.Client != nil {
		return p.Client
	}
	return &http.Client{Timeout: 30 * time.Minute}
}

func (p MeshServiceAnalysisMeshProvider) ListAlgorithms(ctx context.Context) ([]AnalysisMeshAlgorithmDescriptor, error) {
	var payload []AnalysisMeshAlgorithmDescriptor
	if err := p.doJSON(ctx, http.MethodGet, "/analysis-mesh/algorithms", nil, &payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func (p MeshServiceAnalysisMeshProvider) Build(ctx context.Context, in AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error) {
	var out AnalysisMeshArtifactManifest
	if err := p.doJSON(ctx, http.MethodPost, "/analysis-mesh/build", in, &out); err != nil {
		return out, err
	}
	return out, nil
}

func (p MeshServiceAnalysisMeshProvider) doJSON(ctx context.Context, method, endpoint string, input, output any) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(p.BaseURL, "/")+endpoint, body)
	if err != nil {
		return err
	}
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := p.client().Do(req)
	if err != nil {
		return fmt.Errorf("analysis_mesh_provider_failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return analysisMeshHTTPError(resp)
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(output); err != nil {
		return fmt.Errorf("analysis_mesh_provider_failed: invalid JSON response: %w", err)
	}
	return nil
}

func analysisMeshHTTPError(resp *http.Response) error {
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	var payload struct {
		Error     string          `json:"error"`
		ErrorCode string          `json:"errorCode"`
		Message   string          `json:"message"`
		Detail    json.RawMessage `json:"detail"`
	}
	_ = json.Unmarshal(data, &payload)
	code := strings.TrimSpace(payload.ErrorCode)
	if code == "" {
		code = strings.TrimSpace(payload.Error)
	}
	if resp.StatusCode == http.StatusTooManyRequests {
		code = "provider_busy"
	}
	if code == "" {
		if resp.StatusCode >= 400 && resp.StatusCode < 500 {
			code = "invalid_parameters"
		} else {
			code = "provider_failed"
		}
	}
	detail := strings.TrimSpace(payload.Message)
	if detail == "" {
		detail = strings.TrimSpace(string(payload.Detail))
	}
	if detail == "" {
		detail = strings.TrimSpace(string(data))
	}
	return &AnalysisMeshProviderError{Code: code, Status: resp.StatusCode, RetryAfter: strings.TrimSpace(resp.Header.Get("Retry-After")), Detail: detail}
}

// InMemoryAnalysisMeshProvider is intentionally small and useful for handler/unit tests.
type InMemoryAnalysisMeshProvider struct {
	Algorithms []AnalysisMeshAlgorithmDescriptor
	BuildFunc  func(context.Context, AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error)
}

func (p InMemoryAnalysisMeshProvider) ListAlgorithms(context.Context) ([]AnalysisMeshAlgorithmDescriptor, error) {
	return append([]AnalysisMeshAlgorithmDescriptor(nil), p.Algorithms...), nil
}
func (p InMemoryAnalysisMeshProvider) Build(ctx context.Context, in AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error) {
	if p.BuildFunc == nil {
		return AnalysisMeshArtifactManifest{}, &AnalysisMeshProviderError{Code: "not_configured", Status: http.StatusServiceUnavailable}
	}
	return p.BuildFunc(ctx, in)
}
