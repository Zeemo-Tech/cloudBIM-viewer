package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// RebarComputeProvider is the boundary between HTTP handlers and geometry work.
type RebarComputeProvider interface {
	ListAlgorithms(context.Context) ([]RebarAlgorithmDescriptor, error)
	Compute(context.Context, RebarComputeRequest) (RebarArtifactManifest, error)
}

type RebarAlgorithmDescriptor struct {
	ID                string         `json:"id"`
	Version           string         `json:"version"`
	Name              string         `json:"name,omitempty"`
	AnalysisSchema    string         `json:"analysisSchema,omitempty"`
	ParameterSchema   map[string]any `json:"parameterSchema,omitempty"`
	InputOptionSchema map[string]any `json:"inputOptionSchema,omitempty"`
	UIHints           map[string]any `json:"uiHints,omitempty"`
	Capabilities      map[string]any `json:"capabilities"`
	Visualization     any            `json:"visualization,omitempty"`
}
type RebarProviderError struct {
	Code       string
	Status     int
	RetryAfter string
}

func (e *RebarProviderError) Error() string { return e.Code }

type RebarComputeRequest struct {
	PointCloudPath    string         `json:"point_cloud_path"`
	PointCloudFormat  string         `json:"point_cloud_format"`
	SourceTilesetPath string         `json:"source_tileset_path"`
	OutputDirectory   string         `json:"output_directory"`
	ArtifactVersion   string         `json:"artifact_version"`
	Algorithm         string         `json:"algorithm"`
	InputOptions      map[string]any `json:"input_options"`
	Parameters        map[string]any `json:"parameters"`
}
type RebarArtifactManifest struct {
	Schema          string `json:"schema"`
	ArtifactVersion string `json:"artifactVersion"`
	Algorithm       struct {
		ID      string `json:"id"`
		Version string `json:"version"`
	} `json:"algorithm"`
	AnalysisSchema      string `json:"analysisSchema"`
	Capabilities        any    `json:"capabilities"`
	InputOptions        any    `json:"inputOptions"`
	EffectiveParameters any    `json:"effectiveParameters"`
	Summary             any    `json:"summary"`
	Visualization       any    `json:"visualization,omitempty"`
	ResultPath          string `json:"resultPath"`
	TilesetPath         string `json:"tilesetPath"`
	ManifestPath        string `json:"manifestPath"`
	ContentHash         string `json:"contentHash"`
	ByteSize            int64  `json:"byteSize"`
}
type MeshServiceRebarComputeProvider struct {
	BaseURL string
	Client  *http.Client
}

func (p MeshServiceRebarComputeProvider) client() *http.Client {
	if p.Client != nil {
		return p.Client
	}
	return &http.Client{Timeout: 30 * time.Minute}
}
func (p MeshServiceRebarComputeProvider) ListAlgorithms(ctx context.Context) ([]RebarAlgorithmDescriptor, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(p.BaseURL, "/")+"/rebar/algorithms", nil)
	if err != nil {
		return nil, err
	}
	resp, err := p.client().Do(req)
	if err != nil {
		return nil, fmt.Errorf("provider_failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("provider_failed: service returned %d", resp.StatusCode)
	}
	var payload struct {
		Algorithms []RebarAlgorithmDescriptor `json:"algorithms"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("provider_failed: invalid algorithms response")
	}
	return payload.Algorithms, nil
}
func (p MeshServiceRebarComputeProvider) Compute(ctx context.Context, in RebarComputeRequest) (RebarArtifactManifest, error) {
	body, err := json.Marshal(in)
	if err != nil {
		return RebarArtifactManifest{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(p.BaseURL, "/")+"/rebar/compute", bytes.NewReader(body))
	if err != nil {
		return RebarArtifactManifest{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.client().Do(req)
	if err != nil {
		return RebarArtifactManifest{}, fmt.Errorf("provider_failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		var payload struct {
			Error     string `json:"error"`
			ErrorCode string `json:"errorCode"`
			Detail    struct {
				Code string `json:"code"`
			} `json:"detail"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&payload)
		code := payload.ErrorCode
		if code == "" {
			code = payload.Error
		}
		if code == "" {
			code = payload.Detail.Code
		}
		if resp.StatusCode == http.StatusConflict || resp.StatusCode == http.StatusTooManyRequests {
			code = "provider_busy"
		}
		if code == "" {
			if resp.StatusCode >= 400 && resp.StatusCode < 500 {
				code = "invalid_parameters"
			} else {
				code = "provider_failed"
			}
		}
		return RebarArtifactManifest{}, &RebarProviderError{
			Code:       code,
			Status:     resp.StatusCode,
			RetryAfter: strings.TrimSpace(resp.Header.Get("Retry-After")),
		}
	}
	var out RebarArtifactManifest
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, fmt.Errorf("provider_failed: invalid manifest")
	}
	return out, nil
}
