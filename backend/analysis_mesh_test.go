package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

type fakeAnalysisMeshProvider struct {
	t     *testing.T
	calls int
}

type blockingAnalysisMeshProvider struct{}

func (blockingAnalysisMeshProvider) ListAlgorithms(ctx context.Context) ([]AnalysisMeshAlgorithmDescriptor, error) {
	<-ctx.Done()
	return nil, ctx.Err()
}
func (blockingAnalysisMeshProvider) Build(context.Context, AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error) {
	return AnalysisMeshArtifactManifest{}, errors.New("unexpected build")
}

func (provider *fakeAnalysisMeshProvider) ListAlgorithms(context.Context) ([]AnalysisMeshAlgorithmDescriptor, error) {
	return []AnalysisMeshAlgorithmDescriptor{{ID: "pymeshlab-isotropic-component-v1", ImplementationVersion: "1", ContractVersion: "1"}}, nil
}

func (provider *fakeAnalysisMeshProvider) Build(_ context.Context, request AnalysisMeshBuildRequest) (AnalysisMeshArtifactManifest, error) {
	provider.calls++
	if err := os.MkdirAll(request.OutputPath, 0750); err != nil {
		return AnalysisMeshArtifactManifest{}, err
	}
	manifest := writeAnalysisMeshArtifact(provider.t, request.OutputPath)
	manifest.Algorithm.ID = request.AlgorithmID
	manifest.Algorithm.ImplementationVersion = "1"
	manifest.Algorithm.ContractVersion = "1"
	manifest.Algorithm.EffectiveParameters = request.Parameters
	rewriteAnalysisMeshManifest(provider.t, request.OutputPath, manifest)
	return manifest, nil
}

func TestAnalysisMeshProviderDescriptorAndError(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/analysis-mesh/algorithms" {
			_ = json.NewEncoder(w).Encode([]map[string]any{{"id": "ifc-v1", "label": "IFC", "implementationVersion": "1", "contractVersion": "v1", "capabilities": []string{}, "parameterSchema": map[string]any{}, "defaults": map[string]any{}}})
			return
		}
		w.Header().Set("Retry-After", "3")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"errorCode":"overloaded","message":"busy"}`))
	}))
	defer s.Close()
	p := MeshServiceAnalysisMeshProvider{BaseURL: s.URL, Client: s.Client()}
	algorithms, err := p.ListAlgorithms(context.Background())
	if err != nil || len(algorithms) != 1 || algorithms[0].ID != "ifc-v1" {
		t.Fatalf("algorithms = %#v, %v", algorithms, err)
	}
	_, err = p.Build(context.Background(), AnalysisMeshBuildRequest{AlgorithmID: "ifc-v1"})
	providerErr, ok := err.(*AnalysisMeshProviderError)
	if !ok || providerErr.Code != "provider_busy" || providerErr.Status != 429 || providerErr.RetryAfter != "3" {
		t.Fatalf("error = %#v", err)
	}
}

func TestValidateAnalysisMeshManifestRejectsEscapeSymlinkAndHashMismatch(t *testing.T) {
	root := t.TempDir()
	m := writeAnalysisMeshArtifact(t, root)
	if _, err := ValidateAnalysisMeshManifest(root, m); err != nil {
		t.Fatalf("valid manifest: %v", err)
	}
	bad := m.Files["tileset.json"]
	m.Files["../escape"] = bad
	delete(m.Files, "tileset.json")
	if _, err := ValidateAnalysisMeshManifest(root, m); err == nil {
		t.Fatal("accepted path escape")
	}
	m = writeAnalysisMeshArtifact(t, root)
	target := filepath.Join(root, "tileset.json")
	if err := os.Remove(target); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("components.json", target); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateAnalysisMeshManifest(root, m); err == nil {
		t.Fatal("accepted symlink")
	}
	m = writeAnalysisMeshArtifact(t, root)
	bad = m.Files["tileset.json"]
	bad.ContentHash = "0000000000000000000000000000000000000000000000000000000000000000"
	m.Files["tileset.json"] = bad
	rewriteAnalysisMeshManifest(t, root, m)
	if _, err := ValidateAnalysisMeshManifest(root, m); err == nil {
		t.Fatal("accepted wrong hash")
	}
}

func TestValidateAnalysisMeshManifestRejectsInvalidPartRegistry(t *testing.T) {
	validDocument := func(manifest AnalysisMeshArtifactManifest) map[string]any {
		tileFile := manifest.Files["tiles/tile-000000.glb"]
		return map[string]any{
			"schema": "analysis-mesh-components-v1",
			"tree":   map[string]any{"id": "root", "children": []any{map[string]any{"id": "G1", "children": []any{}}}},
			"components": []any{map[string]any{
				"ifcGlobalId": "G1",
				"parts": []any{map[string]any{
					"partId": "G1:node", "nodeName": "G1", "faceCount": 1,
					"positionHash": strings.Repeat("a", 64), "tiles": []any{"tile-000000"},
				}},
			}},
			"tiles": []any{map[string]any{
				"tileId": "tile-000000", "uri": "tiles/tile-000000.glb",
				"ifcGlobalId": "G1", "partId": "G1:node",
				"positionHash": strings.Repeat("a", 64), "vertexCount": 3, "faceCount": 1,
				"byteLength": tileFile.ByteSize, "sha256": tileFile.ContentHash,
			}},
		}
	}
	cases := map[string]func(map[string]any){
		"missing parts": func(document map[string]any) {
			delete(document["components"].([]any)[0].(map[string]any), "parts")
		},
		"unknown part": func(document map[string]any) {
			document["tiles"].([]any)[0].(map[string]any)["partId"] = "G1:unknown"
		},
		"inconsistent tile list": func(document map[string]any) {
			component := document["components"].([]any)[0].(map[string]any)
			component["parts"].([]any)[0].(map[string]any)["tiles"] = []any{"tile-missing"}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			manifest := writeAnalysisMeshArtifact(t, root)
			document := validDocument(manifest)
			mutate(document)
			manifest = rewriteAnalysisMeshComponentsArtifact(t, root, manifest, document)
			if _, err := ValidateAnalysisMeshManifest(root, manifest); err == nil {
				t.Fatal("accepted invalid component part registry")
			}
		})
	}
}

func TestAnalysisMeshResourceMustBeDeclared(t *testing.T) {
	root := t.TempDir()
	manifest := AnalysisMeshArtifactManifest{EntryPath: "tileset.json", ContentHash: strings.Repeat("a", 64), Files: map[string]AnalysisMeshArtifactFile{"tileset.json": {ContentHash: strings.Repeat("b", 64), ByteSize: 2}, "tiles/tile.glb": {ContentHash: strings.Repeat("c", 64), ByteSize: 3}}}
	row, err := AnalysisMeshDerivativeRow(1, AnalysisMeshPaths{RelativePath: "derivatives/analysis-mesh/am-test"}, manifest, 5, "fingerprint")
	if err != nil {
		t.Fatal(err)
	}
	rewriteAnalysisMeshManifest(t, root, manifest)
	if !analysisMeshResourceDeclared(root, row, "/manifest.json") || !analysisMeshResourceDeclared(root, row, "/tileset.json") || !analysisMeshResourceDeclared(root, row, "/tiles/tile.glb") || analysisMeshResourceDeclared(root, row, "/undeclared.bin") {
		t.Fatal("analysis-mesh resource registry was not enforced")
	}
	manifest.Algorithm.ImplementationVersion = "semantic-tamper"
	rewriteAnalysisMeshManifest(t, root, manifest)
	if analysisMeshResourceDeclared(root, row, "/manifest.json") {
		t.Fatal("analysis-mesh resource accepted a manifest that differs from the database snapshot")
	}
}

func TestMeshProviderGateSerializesAndHonorsCancellation(t *testing.T) {
	a := newApp(config{WorkerCount: 1})
	firstRelease, err := a.acquireMeshProvider(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := a.acquireMeshProvider(cancelled); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled acquire = %v", err)
	}
	firstRelease()
	firstRelease()
	secondRelease, err := a.acquireMeshProvider(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	secondRelease()
}

func TestAnalysisMeshFingerprintAndLifecycle(t *testing.T) {
	a := Asset{ID: 9, SourceSize: 42, Dir: t.TempDir()}
	f1, err := AnalysisMeshRequestFingerprint(a, "input", "ifc-v1", map[string]any{"b": 2, "a": 1}, map[string]any{"x": true})
	if err != nil {
		t.Fatal(err)
	}
	f2, err := AnalysisMeshRequestFingerprint(a, "input", "ifc-v1", map[string]any{"a": 1, "b": 2}, map[string]any{"x": true})
	if err != nil || f1 != f2 {
		t.Fatalf("fingerprints %q %q: %v", f1, f2, err)
	}
	f3, err := AnalysisMeshRequestFingerprint(a, "input", "ifc-v1", map[string]any{"a": 1, "b": 2, "implementationVersion": "2"}, map[string]any{"x": true})
	if err != nil || f3 == f2 {
		t.Fatalf("algorithm implementation upgrade did not change fingerprint: %q %q, %v", f2, f3, err)
	}
	paths, err := AnalysisMeshStoragePaths(a, AnalysisMeshArtifactVersion(f1))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(paths.StagingPath, 0750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(paths.StagingPath, "manifest.json"), []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := PublishAnalysisMeshArtifact(paths.StagingPath, paths.FinalPath); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(paths.FinalPath, "manifest.json")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(paths.StagingPath); !os.IsNotExist(err) {
		t.Fatalf("staging still exists: %v", err)
	}
}

func TestAnalysisMeshDerivativeAndJobTransitions(t *testing.T) {
	m := AnalysisMeshArtifactManifest{ArtifactVersion: "analysis-mesh-artifact-v1", EntryPath: "tileset.json", ContentHash: "hash"}
	m.Algorithm.ID, m.Algorithm.ImplementationVersion, m.Algorithm.ContractVersion = "ifc-v1", "1", "v1"
	m.Algorithm.EffectiveParameters = map[string]any{}
	row, err := AnalysisMeshDerivativeRow(3, AnalysisMeshPaths{RelativePath: "analysis-mesh/am-abc"}, m, 11, "request")
	if err != nil || row.Kind != analysisMeshKind || row.Status != "ready" || row.EntryPath != "tileset.json" || row.Version != "am-abc" {
		t.Fatalf("row = %#v, %v", row, err)
	}
	var job AnalysisMeshJob
	now := time.Now()
	if err := job.Transition(AnalysisMeshQueued, now); err != nil {
		t.Fatal(err)
	}
	if err := job.Transition(AnalysisMeshProcessing, now); err != nil {
		t.Fatal(err)
	}
	if err := job.Transition(AnalysisMeshReady, now); err != nil {
		t.Fatal(err)
	}
	if err := job.Transition(AnalysisMeshQueued, now); err == nil {
		t.Fatal("allowed ready retry")
	}
	job = AnalysisMeshJob{Status: AnalysisMeshFailed}
	if err := job.Transition(AnalysisMeshQueued, now); err != nil || job.Attempts != 1 {
		t.Fatalf("retry = %#v, %v", job, err)
	}
}

func TestAnalysisMeshBuildLifecycleCachesAndPublishesImmutableVersion(t *testing.T) {
	gin.SetMode(gin.TestMode)
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open("file:analysis_mesh_lifecycle?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&DBAsset{}, &DBAssetDerivative{}); err != nil {
		t.Fatal(err)
	}
	assetDir := filepath.Join(root, "assets", "1")
	if err := os.MkdirAll(assetDir, 0750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(assetDir, "model.glb"), []byte("model"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(assetDir, "metadata.json"), []byte(`{"tree":{}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&DBAsset{ID: 1, OwnerID: 7, Type: "bim", Status: "ready", SourceName: "model.ifc", SourceSize: 5, Dir: assetDir}).Error; err != nil {
		t.Fatal(err)
	}
	provider := &fakeAnalysisMeshProvider{t: t}
	a := newApp(config{DataDir: root, MeshServiceStorageDir: root, MeshServiceURL: "http://mesh", WorkerCount: 1})
	a.db = db
	a.analysisMeshProvider = provider
	post := func(force bool) *httptest.ResponseRecorder {
		body := `{"algorithmId":"pymeshlab-isotropic-component-v1","parameters":{"targetEdgeLength":0.02}}`
		if force {
			body = `{"algorithmId":"pymeshlab-isotropic-component-v1","parameters":{"targetEdgeLength":0.02},"force":true}`
		}
		context, response := rebarContext(http.MethodPost, "/assets/1/analysis-mesh", body, 7)
		a.analysisMeshBuild(context)
		return response
	}
	response := post(false)
	if response.Code != http.StatusOK || provider.calls != 1 || !bytes.Contains(response.Body.Bytes(), []byte(`"cached":false`)) {
		t.Fatalf("first=%d %s calls=%d", response.Code, response.Body.String(), provider.calls)
	}
	var first DBAssetDerivative
	if err := db.Where("asset_id = ? AND kind = ?", 1, analysisMeshKind).First(&first).Error; err != nil {
		t.Fatal(err)
	}
	if !a.validAnalysisMeshRow(assetFromDB(DBAsset{ID: 1, OwnerID: 7, Type: "bim", Status: "ready", Dir: assetDir}), first) {
		t.Fatal("published analysis mesh row is invalid")
	}
	response = post(false)
	if response.Code != http.StatusOK || provider.calls != 1 || !bytes.Contains(response.Body.Bytes(), []byte(`"cached":true`)) {
		t.Fatalf("cached=%d %s calls=%d", response.Code, response.Body.String(), provider.calls)
	}
	response = post(true)
	if response.Code != http.StatusOK || provider.calls != 2 {
		t.Fatalf("force=%d %s calls=%d", response.Code, response.Body.String(), provider.calls)
	}
	var latest DBAssetDerivative
	if err := db.Where("asset_id = ? AND kind = ?", 1, analysisMeshKind).First(&latest).Error; err != nil {
		t.Fatal(err)
	}
	if latest.Version == first.Version {
		t.Fatal("forced build did not publish a fresh immutable version")
	}
	context, latestResponse := rebarContext(http.MethodGet, "/assets/1/analysis-mesh/latest", "", 7)
	a.analysisMeshLatest(context)
	if latestResponse.Code != http.StatusOK || !bytes.Contains(latestResponse.Body.Bytes(), []byte(latest.Version)) {
		t.Fatalf("latest=%d %s", latestResponse.Code, latestResponse.Body.String())
	}
	latestRoot := filepath.Join(assetDir, filepath.FromSlash(latest.RelativePath))
	latestManifestBytes, err := os.ReadFile(filepath.Join(latestRoot, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var latestManifest AnalysisMeshArtifactManifest
	if err := json.Unmarshal(latestManifestBytes, &latestManifest); err != nil {
		t.Fatal(err)
	}
	latestManifest.Algorithm.ImplementationVersion = "tampered-without-changing-file-content-hash"
	rewriteAnalysisMeshManifest(t, latestRoot, latestManifest)
	if a.validAnalysisMeshRow(assetFromDB(DBAsset{ID: 1, OwnerID: 7, Type: "bim", Status: "ready", Dir: assetDir}), latest) {
		t.Fatal("database metadata did not bind analysis-mesh manifest semantics")
	}
	resourceContext, resourceResponse := rebarContext(http.MethodGet, "/assets/1/representations/analysis-mesh/"+latest.Version+"/manifest.json", "", 7)
	resourceContext.Params = gin.Params{
		{Key: "id", Value: "1"},
		{Key: "kind", Value: analysisMeshKind},
		{Key: "version", Value: latest.Version},
		{Key: "path", Value: "/manifest.json"},
	}
	a.derivativeResource(resourceContext)
	if resourceResponse.Code != http.StatusNotFound {
		t.Fatalf("versioned resource served a semantically tampered manifest: %d %s", resourceResponse.Code, resourceResponse.Body.String())
	}
}

func TestMeshRecoveryDoesNotBlockStartupWhenQueueIsFull(t *testing.T) {
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open("file:analysis_mesh_recovery?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&DBUpload{}, &DBAsset{}, &DBAssetDerivative{}, &DBC2MResult{}); err != nil {
		t.Fatal(err)
	}
	for index := 1; index <= 40; index++ {
		assetDir := filepath.Join(root, fmt.Sprintf("asset-%d", index))
		if err := os.MkdirAll(assetDir, 0750); err != nil {
			t.Fatal(err)
		}
		for _, name := range []string{"model.glb", "metadata.json", "mesh_remesh.ply"} {
			if err := os.WriteFile(filepath.Join(assetDir, name), []byte("fixture"), 0600); err != nil {
				t.Fatal(err)
			}
		}
		if err := db.Create(&DBAsset{ID: int64(index), OwnerID: 1, Type: "bim", Status: "ready", SourceName: "model.ifc", SourceSize: 7, Dir: assetDir, RemeshStatus: "succeeded"}).Error; err != nil {
			t.Fatal(err)
		}
	}
	a := newApp(config{DataDir: root, MeshServiceStorageDir: root, MeshServiceURL: "http://mesh", WorkerCount: 1})
	a.db = db
	a.analysisMeshProvider = blockingAnalysisMeshProvider{}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- a.startWorkers(ctx) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		cancel()
		t.Fatal("HTTP startup was blocked by mesh recovery queue backpressure")
	}
	cancel()
	a.waitWorkers()
}

func writeAnalysisMeshArtifact(t *testing.T, root string) AnalysisMeshArtifactManifest {
	t.Helper()
	tile := []byte("glb")
	tileHash := sha256.Sum256(tile)
	tileHashText := hex.EncodeToString(tileHash[:])
	components, err := json.Marshal(map[string]any{
		"schema":     "analysis-mesh-components-v1",
		"tree":       map[string]any{"id": "root", "children": []map[string]any{{"id": "G1", "children": []any{}}}},
		"components": []map[string]any{{"ifcGlobalId": "G1", "parts": []map[string]any{{"partId": "G1:node", "nodeName": "G1", "faceCount": 1, "positionHash": strings.Repeat("a", 64), "tiles": []string{"tile-000000"}}}}},
		"tiles":      []map[string]any{{"tileId": "tile-000000", "uri": "tiles/tile-000000.glb", "ifcGlobalId": "G1", "partId": "G1:node", "positionHash": strings.Repeat("a", 64), "vertexCount": 3, "faceCount": 1, "byteLength": len(tile), "sha256": tileHashText}},
	})
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{"tileset.json": []byte("{}"), "components.json": components, "metrics.json": []byte(`{"schema":"analysis-mesh-metrics-v1","components":[]}`), "tiles/tile-000000.glb": tile}
	m := AnalysisMeshArtifactManifest{ArtifactVersion: "analysis-mesh-artifact-v1", Immutable: true, EntryPath: "tileset.json", ComponentsPath: "components.json", MetricsPath: "metrics.json", ComponentCount: 1, TileCount: 1, FaceCap: 1}
	m.ModelFrame.SourceBounds.Min = []float64{-1, -1, -1}
	m.ModelFrame.SourceBounds.Max = []float64{1, 1, 1}
	m.ModelFrame.NormalizationCenter = []float64{0, 0, 0}
	m.Algorithm.ID, m.Algorithm.ImplementationVersion, m.Algorithm.ContractVersion = "ifc-v1", "1", "v1"
	m.Algorithm.EffectiveParameters = map[string]any{}
	aggregate := sha256.New()
	for _, name := range []string{"components.json", "metrics.json", "tiles/tile-000000.glb", "tileset.json"} {
		data := files[name]
		if err := os.MkdirAll(filepath.Dir(filepath.Join(root, name)), 0750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, name), data, 0600); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(data)
		if m.Files == nil {
			m.Files = map[string]AnalysisMeshArtifactFile{}
		}
		m.Files[name] = AnalysisMeshArtifactFile{ContentHash: hex.EncodeToString(digest[:]), ByteSize: int64(len(data))}
		_, _ = aggregate.Write([]byte(name))
		_, _ = aggregate.Write([]byte{0})
		_, _ = aggregate.Write([]byte(hex.EncodeToString(digest[:])))
	}
	m.ContentHash = hex.EncodeToString(aggregate.Sum(nil))
	rewriteAnalysisMeshManifest(t, root, m)
	return m
}
func rewriteAnalysisMeshManifest(t *testing.T, root string, m AnalysisMeshArtifactManifest) {
	t.Helper()
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}

func rewriteAnalysisMeshComponentsArtifact(t *testing.T, root string, m AnalysisMeshArtifactManifest, document any) AnalysisMeshArtifactManifest {
	t.Helper()
	data, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, m.ComponentsPath), data, 0600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(data)
	m.Files[m.ComponentsPath] = AnalysisMeshArtifactFile{ContentHash: hex.EncodeToString(digest[:]), ByteSize: int64(len(data))}
	names := make([]string, 0, len(m.Files))
	for name := range m.Files {
		names = append(names, name)
	}
	sort.Strings(names)
	aggregate := sha256.New()
	for _, name := range names {
		_, _ = aggregate.Write([]byte(name))
		_, _ = aggregate.Write([]byte{0})
		_, _ = aggregate.Write([]byte(strings.ToLower(m.Files[name].ContentHash)))
	}
	m.ContentHash = hex.EncodeToString(aggregate.Sum(nil))
	rewriteAnalysisMeshManifest(t, root, m)
	return m
}
