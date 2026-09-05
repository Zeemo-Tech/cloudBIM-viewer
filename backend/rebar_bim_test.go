package main

import (
	"encoding/json"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"os"
	"path/filepath"
	"testing"
)

func TestRebarBimPriorOwnershipAndInvalidation(t *testing.T) {
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open(filepath.Join(root, "test.db")), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err = db.AutoMigrate(&DBAsset{}, &DBAlignment{}, &DBUpload{}); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(root, "assets", "4")
	if err = os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{"model.glb": "geometry", "metadata.json": "{}"} {
		if err = os.WriteFile(filepath.Join(dir, name), []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	asset := DBAsset{ID: 4, OwnerID: 7, Type: "bim", Status: "ready", Dir: dir}
	if err = db.Create(&asset).Error; err != nil {
		t.Fatal(err)
	}
	matrix := []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
	data, _ := json.Marshal(matrix)
	alignment := DBAlignment{ScanID: 5, BimID: 4, OwnerID: 7, MatrixJSON: string(data)}
	if err = db.Create(&alignment).Error; err != nil {
		t.Fatal(err)
	}
	a := newApp(config{DataDir: root, MeshServiceStorageDir: "/storage", WorkerCount: 1})
	a.db = db
	selection := &rebarBimSelection{BimAssetID: 4}
	first, err := a.resolveRebarBimPrior(5, 7, selection)
	if err != nil || first.IFCPath != "" || first.ModelPath != "/storage/assets/4/model.glb" {
		t.Fatalf("prior=%+v err=%v", first, err)
	}
	if _, err = a.resolveRebarBimPrior(5, 8, selection); err == nil {
		t.Fatal("other owner accessed BIM")
	}
	if _, err = a.resolveRebarBimPrior(6, 7, selection); err == nil {
		t.Fatal("missing alignment accepted")
	}
	matrix[12] = .02
	data, _ = json.Marshal(matrix)
	if err = db.Model(&alignment).Update("matrix_json", string(data)).Error; err != nil {
		t.Fatal(err)
	}
	changed, err := a.resolveRebarBimPrior(5, 7, selection)
	if err != nil || changed.Fingerprint == first.Fingerprint {
		t.Fatal("matrix change did not invalidate prior")
	}
	if err = os.WriteFile(filepath.Join(dir, "model.glb"), []byte("changed geometry"), 0600); err != nil {
		t.Fatal(err)
	}
	changedModel, err := a.resolveRebarBimPrior(5, 7, selection)
	if err != nil || changedModel.Fingerprint == changed.Fingerprint {
		t.Fatal("geometry change did not invalidate prior")
	}
	outside := filepath.Join(t.TempDir(), "private.glb")
	if err = os.WriteFile(outside, []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(filepath.Join(dir, "model.glb")); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(outside, filepath.Join(dir, "model.glb")); err != nil {
		t.Fatal(err)
	}
	if _, err = a.resolveRebarBimPrior(5, 7, selection); err == nil {
		t.Fatal("symlink escaped data root")
	}
}
