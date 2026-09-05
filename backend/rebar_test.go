package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func validRebarManifest() RebarArtifactManifest {
	var m RebarArtifactManifest
	m.Schema, m.ArtifactVersion, m.AnalysisSchema, m.ContentHash = "rebar-artifact-manifest-v1", "v1", "rebar-analysis-v1", hashBytes([]byte("result.json\x00{}tiles/tileset.json\x00{}"))
	m.Algorithm.ID, m.Algorithm.Version = "geometric-v2", "1"
	m.TilesetPath, m.ResultPath, m.ManifestPath = "tiles/tileset.json", "result.json", "manifest.json"
	m.ByteSize = 6
	return m
}

type fakeRebarProvider struct{ calls int }

func (p *fakeRebarProvider) ListAlgorithms(context.Context) ([]RebarAlgorithmDescriptor, error) {
	return []RebarAlgorithmDescriptor{{ID: "geometric-v2", Version: "1", Capabilities: map[string]any{}}}, nil
}
func (p *fakeRebarProvider) Compute(_ context.Context, r RebarComputeRequest) (RebarArtifactManifest, error) {
	p.calls++
	if err := os.MkdirAll(filepath.Join(r.OutputDirectory, "tiles"), 0755); err != nil {
		return RebarArtifactManifest{}, err
	}
	_ = os.WriteFile(filepath.Join(r.OutputDirectory, "result.json"), []byte(`{"result":true}`), 0644)
	_ = os.WriteFile(filepath.Join(r.OutputDirectory, "tiles", "tileset.json"), []byte(`{}`), 0644)
	_ = os.WriteFile(filepath.Join(r.OutputDirectory, "tiles", "0.pnts"), []byte("pnts"), 0644)
	// Python's tree hash excludes manifest itself.
	h := sha256.New()
	for _, x := range []string{"result.json", "tiles/0.pnts", "tiles/tileset.json"} {
		b, _ := os.ReadFile(filepath.Join(r.OutputDirectory, x))
		_, _ = h.Write([]byte(x))
		_, _ = h.Write([]byte{0})
		_, _ = h.Write(b)
	}
	var m RebarArtifactManifest
	m.Schema = "rebar-artifact-manifest-v1"
	m.ArtifactVersion = r.ArtifactVersion
	m.AnalysisSchema = "rebar-analysis-v1"
	m.Algorithm.ID, m.Algorithm.Version = "geometric-v2", "1"
	m.Capabilities = map[string]any{}
	m.InputOptions = map[string]any{}
	m.EffectiveParameters = map[string]any{}
	m.Summary = map[string]any{}
	m.ResultPath = "result.json"
	m.TilesetPath = "tiles/tileset.json"
	m.ManifestPath = "manifest.json"
	m.ContentHash = hex.EncodeToString(h.Sum(nil))
	m.ByteSize = int64(len(`{"result":true}`) + 2 + 4)
	for range 3 {
		manifest, _ := json.Marshal(m)
		m.ByteSize = int64(len(`{"result":true}`)+2+4) + int64(len(manifest))
	}
	manifest, _ := json.Marshal(m)
	_ = os.WriteFile(filepath.Join(r.OutputDirectory, "manifest.json"), manifest, 0644)
	return m, nil
}
func rebarContext(method, path, body string, user int64) (*gin.Context, *httptest.ResponseRecorder) {
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest(method, path, bytes.NewBufferString(body))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Params = []gin.Param{{Key: "id", Value: "1"}}
	c.Set("userID", user)
	return c, w
}
func TestRebarComputeLifecycle(t *testing.T) {
	gin.SetMode(gin.TestMode)
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open("file:rebar_lifecycle?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err = db.AutoMigrate(&DBAsset{}, &DBAssetDerivative{}); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(root, "assets", "1")
	if err = os.MkdirAll(filepath.Join(dir, "tiles"), 0755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(dir, "source"), []byte("scan"), 0644)
	_ = os.WriteFile(filepath.Join(dir, "tiles", "tileset.json"), []byte("{}"), 0644)
	asset := DBAsset{ID: 1, OwnerID: 7, Type: "pointcloud", Status: "ready", SourceName: "scan.las", SourceSize: 4, Dir: dir}
	if err = db.Create(&asset).Error; err != nil {
		t.Fatal(err)
	}
	fake := &fakeRebarProvider{}
	a := newApp(config{DataDir: root, MeshServiceStorageDir: root, WorkerCount: 1})
	a.db = db
	a.rebarProvider = fake
	post := func(force bool) *httptest.ResponseRecorder {
		q := ""
		if force {
			q = "?force=true"
		}
		c, w := rebarContext(http.MethodPost, "/assets/1/rebar-segmentation"+q, `{}`, 7)
		a.rebarCompute(c)
		return w
	}
	w := post(false)
	if w.Code != 200 || fake.calls != 1 || !bytes.Contains(w.Body.Bytes(), []byte(`"cached":false`)) {
		t.Fatalf("first=%d %s calls=%d", w.Code, w.Body.String(), fake.calls)
	}
	var row DBAssetDerivative
	if err = db.Where("asset_id=? AND kind=?", 1, rebarKind).First(&row).Error; err != nil {
		t.Fatal(err)
	}
	first := row.Version
	w = post(false)
	if w.Code != 200 || fake.calls != 1 || !bytes.Contains(w.Body.Bytes(), []byte(`"cached":true`)) {
		t.Fatalf("cache=%d %s calls=%d", w.Code, w.Body.String(), fake.calls)
	}
	w = post(true)
	if w.Code != 200 || fake.calls != 2 {
		t.Fatalf("force=%d calls=%d", w.Code, fake.calls)
	}
	if err = db.First(&row, "asset_id=? AND kind=?", 1, rebarKind).Error; err != nil {
		t.Fatal(err)
	}
	if row.Version == first {
		t.Fatal("force did not replace version")
	}
	c, w := rebarContext(http.MethodGet, "/assets/1/rebar-segmentation/latest", "", 7)
	a.rebarLatest(c)
	if w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte(row.Version)) {
		t.Fatalf("latest=%d %s", w.Code, w.Body.String())
	}
	c, w = rebarContext(http.MethodGet, "/assets/1/rebar-segmentation/versions/"+row.Version+"/result", "", 7)
	c.Params = append(c.Params, gin.Param{Key: "version", Value: row.Version})
	a.rebarResource(c)
	if w.Code != 200 {
		t.Fatalf("result=%d", w.Code)
	}
	c, w = rebarContext(http.MethodHead, "/assets/1/rebar-segmentation/versions/"+row.Version+"/tiles/tileset.json", "", 7)
	c.Params = gin.Params{{Key: "id", Value: "1"}, {Key: "version", Value: row.Version}, {Key: "path", Value: "/tileset.json"}}
	a.rebarResource(c)
	if w.Code != 200 {
		t.Fatalf("head tile=%d %s", w.Code, w.Body.String())
	}
	// A corrupt latest manifest must neither be served as latest nor hit cache.
	if err := os.Remove(filepath.Join(dir, row.RelativePath, "manifest.json")); err != nil {
		t.Fatal(err)
	}
	c, w = rebarContext(http.MethodGet, "/assets/1/rebar-segmentation/latest", "", 7)
	a.rebarLatest(c)
	if w.Code != 404 {
		t.Fatalf("damaged latest=%d", w.Code)
	}
	w = post(false)
	if w.Code != 200 || fake.calls != 3 {
		t.Fatalf("damaged cache=%d calls=%d", w.Code, fake.calls)
	}
}

func TestRebarManifestRequiresContainedCanonicalTileset(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "tiles"), 0755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"tiles/tileset.json", "result.json", "manifest.json"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("{}"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	m := validRebarManifest()
	for range 3 {
		raw, _ := json.Marshal(m)
		m.ByteSize = int64(4 + len(raw))
	}
	raw, _ := json.Marshal(m)
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), raw, 0644); err != nil {
		t.Fatal(err)
	}
	// The test fixture's tree hash follows the same path+NUL+bytes definition.
	m.ContentHash = hashBytes([]byte("result.json\x00{}tiles/tileset.json\x00{}"))
	raw, _ = json.Marshal(m)
	_ = os.WriteFile(filepath.Join(root, "manifest.json"), raw, 0644)
	if entry, size, err := rebarManifest(root, m); err != nil || entry != "tiles/tileset.json" || size == 0 {
		t.Fatalf("valid manifest: entry=%q size=%d err=%v", entry, size, err)
	}
	bad := m
	bad.ResultPath = "../secret"
	if _, _, err := rebarManifest(root, bad); err == nil {
		t.Fatal("accepted escaping result path")
	}
	bad = m
	bad.Algorithm.ID = "malicious-v2"
	if _, _, err := rebarManifest(root, bad); err == nil {
		t.Fatal("accepted provider manifest that disagrees with disk metadata")
	}
}

func TestRebarFormatUsesSourceNameWhenStoredSourceHasNoExtension(t *testing.T) {
	if got := rebarFormat(Asset{SourceName: "scan.LAZ"}); got != "laz" {
		t.Fatalf("format = %q", got)
	}
}

func TestMeshServiceRebarProviderPreservesDescriptorAndErrorContract(t *testing.T) {
	computeCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/rebar/algorithms":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"algorithms":[{"id":"next","version":"3","capabilities":{},"inputOptionSchema":{"type":"object"}}]}`))
		case "/rebar/compute":
			computeCalls++
			w.Header().Set("Content-Type", "application/json")
			if computeCalls == 1 {
				w.Header().Set("Retry-After", "5")
				w.WriteHeader(http.StatusTooManyRequests)
				_, _ = w.Write([]byte(`{"errorCode":"artifact_invalid"}`))
				return
			}
			w.WriteHeader(http.StatusUnprocessableEntity)
			_, _ = w.Write([]byte(`{"errorCode":"artifact_invalid"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	provider := MeshServiceRebarComputeProvider{BaseURL: server.URL, Client: server.Client()}
	algorithms, err := provider.ListAlgorithms(context.Background())
	if err != nil || len(algorithms) != 1 || algorithms[0].InputOptionSchema["type"] != "object" {
		t.Fatalf("algorithms=%#v err=%v", algorithms, err)
	}
	_, err = provider.Compute(context.Background(), RebarComputeRequest{})
	var providerErr *RebarProviderError
	if !errors.As(err, &providerErr) || providerErr.Code != "provider_busy" || providerErr.RetryAfter != "5" {
		t.Fatalf("provider error=%#v", err)
	}
	_, err = provider.Compute(context.Background(), RebarComputeRequest{})
	if !errors.As(err, &providerErr) || providerErr.Code != "artifact_invalid" || providerErr.Status != http.StatusUnprocessableEntity {
		t.Fatalf("artifact error=%#v", err)
	}
}
