package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fineTestClassification(t *testing.T) (*app, Asset, DBAssetDerivative) {
	t.Helper()
	a, scan := denoiseTestApp(t)
	manifest := installDenoiseService(t, a, nil)
	manifest.InstanceCount = 1
	c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2}`, 10)
	a.computeDenoise(c)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	row, _, err := a.denoiseRow(scan, 2)
	if err != nil {
		t.Fatal(err)
	}
	return a, scan, row
}

func fineTestResult(method string, applied bool) fineAlignmentServiceResponse {
	result := fineAlignmentServiceResponse{Transform: []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}, Method: method, Applied: applied, Diagnostics: map[string]any{"validation": map[string]any{"heldOutBars": 8}}, Metrics: json.RawMessage(`{"initFitness":0.8,"initRmse":0.01,"fineFitness":0.9,"fineRmse":0.005,"deltaTranslationM":0.003,"deltaRotationDeg":0,"elapsedS":1,"sourceTotalPoints":4,"targetPoints":10,"futureMetric":{"value":7}}`)}
	result.Quaternion.Qw = 1
	if applied {
		result.Transform[12] = .003
		result.Translation.Tx = .003
	} else {
		result.Regressed, result.Fallback = true, true
	}
	return result
}

func fineTestServer(t *testing.T, a *app, result fineAlignmentServiceResponse, check func(map[string]any)) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if r.URL.Path != "/align/fine" || json.NewDecoder(r.Body).Decode(&payload) != nil {
			t.Error("unexpected fine alignment request", r.URL.Path)
			w.WriteHeader(400)
			return
		}
		if check != nil {
			check(payload)
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	t.Cleanup(server.Close)
	a.cfg.MeshServiceURL = server.URL
}

func fineTestSaved(t *testing.T, a *app) DBAlignment {
	t.Helper()
	var row DBAlignment
	if err := a.db.Where("scan_id = 1 AND bim_id = 2 AND owner_id = 10").First(&row).Error; err != nil {
		t.Fatal(err)
	}
	return row
}

func fineTestNoAudit(t *testing.T, scan Asset) {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(scan.Dir, "alignment-history", "*.json"))
	if err != nil || len(files) != 0 {
		t.Fatal("rejected result created an audit/application artifact", files, err)
	}
}

func TestFineAlignmentRebarRoutesWithoutRemeshAndArchivesPrevious(t *testing.T) {
	for _, method := range []string{"", "auto", "rebar"} {
		t.Run("method="+method, func(t *testing.T) {
			a, scan, denoise := fineTestClassification(t)
			before := fineTestSaved(t, a)
			instanceHash, _ := fileContentHash(filepath.Join(scan.Dir, denoise.RelativePath, "instance-map.json"))
			a.cfg.MeshServiceStorageDir = "/storage"
			fineTestServer(t, a, fineTestResult(rebarFineAlignmentMethod, true), func(payload map[string]any) {
				if payload["method"] != "rebar" || payload["denoise_dir"] != "/storage/scan/"+filepath.ToSlash(denoise.RelativePath) || payload["mesh_path"] != nil || payload["scan_path"] != nil {
					t.Error("incorrect classified rebar inputs", payload)
				}
			})
			body, _ := json.Marshal(map[string]any{"modelScanFileId": 1, "modelBimFileId": 2, "method": method})
			c, w := rebarContext("POST", "/", string(body), 10)
			a.fineAlignment(c)
			if w.Code != 200 {
				t.Fatal(w.Code, w.Body.String())
			}
			var response struct {
				Data fineAlignmentResponse `json:"data"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil || response.Data.Method != rebarFineAlignmentMethod || response.Data.AuditVersion == "" || !strings.Contains(string(response.Data.Metrics), `"futureMetric"`) || response.Data.Diagnostics["validation"] == nil {
				t.Fatal("additive result fields lost", w.Body.String(), err)
			}
			stored := fineTestSaved(t, a)
			if stored.MatrixJSON == before.MatrixJSON || stored.Tx != .003 || stored.RMSE != .005 {
				t.Fatal("fine result not saved", stored)
			}
			archive, err := os.ReadFile(filepath.Join(scan.Dir, "alignment-history", response.Data.AuditVersion+".json"))
			var audit struct {
				Previous DBAlignment `json:"previousAlignment"`
			}
			if err != nil || json.Unmarshal(archive, &audit) != nil || !sameJSON(audit.Previous, before) {
				t.Fatal("previous full row was not preserved", string(archive), err)
			}
			current, manifest, err := a.denoiseRow(scan, 2)
			newHash, _ := fileContentHash(filepath.Join(scan.Dir, denoise.RelativePath, "instance-map.json"))
			if err != nil || !sameJSON(current, denoise) || newHash != instanceHash || a.denoiseFresh(scan, 2, 10, current, manifest) == nil {
				t.Fatal("classification history changed or old alignment remained fresh", current, err)
			}
			_, prior, _, err := a.denoiseInputs(scan, 2, 10)
			if err != nil || prior.ScanToBim[12] != .003 {
				t.Fatal("next denoise did not resolve the updated alignment", prior, err)
			}
		})
	}
}

func TestFineAlignmentRejectsMissingStaleAndTamperedClassification(t *testing.T) {
	for _, scenario := range []string{"missing", "stale", "instance-tamper", "control-missing", "zero-instances"} {
		for _, method := range []string{"auto", "rebar"} {
			if scenario == "missing" && method == "auto" {
				continue // Covered by the legacy ICP route below.
			}
			t.Run(scenario+"/"+method, func(t *testing.T) {
				a, scan, denoise := fineTestClassification(t)
				switch scenario {
				case "missing":
					a.db.Delete(&DBAssetDerivative{}, "id = ?", denoise.ID)
				case "stale":
					a.db.Model(&DBAlignment{}).Where("scan_id = 1").Update("matrix_json", `[1,0,0,0,0,1,0,0,0,0,1,0,1,0,0,1]`)
				case "instance-tamper":
					os.WriteFile(filepath.Join(scan.Dir, denoise.RelativePath, "instance-map.json"), []byte(`{}`), 0600)
				case "control-missing":
					os.Remove(filepath.Join(scan.Dir, denoise.RelativePath, "control-net.json"))
				case "zero-instances":
					var manifest denoiseManifest
					json.Unmarshal([]byte(denoise.MetadataJSON), &manifest)
					manifest.InstanceCount = 0
					raw, _ := json.Marshal(manifest)
					a.db.Model(&denoise).Update("metadata_json", string(raw))
				}
				before := fineTestSaved(t, a)
				called := false
				fineTestServer(t, a, fineTestResult(rebarFineAlignmentMethod, true), func(map[string]any) { called = true })
				c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2,"method":"`+method+`"}`, 10)
				a.fineAlignment(c)
				if w.Code != 409 || called || !sameJSON(fineTestSaved(t, a), before) {
					t.Fatal("invalid classification was applied or fell back to ICP", w.Code, w.Body.String())
				}
				fineTestNoAudit(t, scan)
			})
		}
	}
}

func TestFineAlignmentConcurrentChangesCannotOverwrite(t *testing.T) {
	for _, changed := range []string{"alignment", "denoise-version", "design-model"} {
		t.Run(changed, func(t *testing.T) {
			a, scan, denoise := fineTestClassification(t)
			before := fineTestSaved(t, a)
			fineTestServer(t, a, fineTestResult(rebarFineAlignmentMethod, true), func(map[string]any) {
				a.c2mMutationMu.Lock()
				defer a.c2mMutationMu.Unlock()
				switch changed {
				case "alignment":
					a.db.Model(&DBAlignment{}).Where("id = ?", before.ID).Updates(map[string]any{"tx": 1, "matrix_json": `[1,0,0,0,0,1,0,0,0,0,1,0,1,0,0,1]`})
				case "denoise-version":
					a.db.Model(&denoise).Update("version", "newer-classification")
				case "design-model":
					os.WriteFile(filepath.Join(a.cfg.DataDir, "bim", "model.glb"), []byte("a newer BIM geometry"), 0600)
				}
			})
			c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2,"method":"rebar"}`, 10)
			a.fineAlignment(c)
			current := fineTestSaved(t, a)
			if w.Code != 409 || (changed == "alignment" && current.Tx != 1) || (changed != "alignment" && !sameJSON(current, before)) {
				t.Fatal("stale result replaced current inputs", w.Code, w.Body.String(), current)
			}
			fineTestNoAudit(t, scan)
		})
	}
}

func TestFineAlignmentRejectedResultsDoNotMutate(t *testing.T) {
	for _, scenario := range []string{"regressed", "invalid-rigid", "inconsistent-quaternion", "wrong-method"} {
		t.Run(scenario, func(t *testing.T) {
			a, scan, _ := fineTestClassification(t)
			before := fineTestSaved(t, a)
			candidate := fineTestResult(rebarFineAlignmentMethod, scenario != "regressed")
			if scenario == "invalid-rigid" {
				candidate.Transform[0] = 2
			} else if scenario == "inconsistent-quaternion" {
				candidate.Quaternion.Qw = .5
			} else if scenario == "wrong-method" {
				candidate.Method = icpFineAlignmentMethod
			}
			fineTestServer(t, a, candidate, nil)
			c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2,"method":"rebar","applyWhenRegressed":true}`, 10)
			a.fineAlignment(c)
			want := 502
			if scenario == "regressed" {
				want = 200
			}
			if w.Code != want || !sameJSON(before, fineTestSaved(t, a)) {
				t.Fatal("rejected result mutated saved alignment", w.Code, w.Body.String())
			}
			fineTestNoAudit(t, scan)
		})
	}
}

func TestFineAlignmentLegacyICPRoute(t *testing.T) {
	for _, method := range []string{"auto", "icp"} {
		t.Run(method, func(t *testing.T) {
			a, scan := denoiseTestApp(t)
			verified := verifiedLegacyRemeshAsset(t, filepath.Join(a.cfg.DataDir, "bim"))
			if err := a.db.Model(&DBAsset{}).Where("id = 2").Updates(map[string]any{"remesh_status": "succeeded", "remesh_algorithm": verified.RemeshAlgorithm, "remesh_params_json": verified.RemeshParamsJSON, "remesh_input_hash": verified.RemeshInputHash, "remesh_implementation_version": verified.RemeshImplementationVersion, "remesh_contract_version": verified.RemeshContractVersion, "remesh_fingerprint": verified.RemeshFingerprint, "remesh_content_hash": verified.RemeshContentHash}).Error; err != nil {
				t.Fatal(err)
			}
			fineTestServer(t, a, fineTestResult(icpFineAlignmentMethod, false), func(payload map[string]any) {
				if payload["method"] != "icp" || payload["denoise_dir"] != nil || payload["scan_path"] == nil || payload["mesh_path"] == nil {
					t.Error("legacy ICP route lost its inputs", payload)
				}
			})
			c, w := rebarContext("POST", "/", `{"modelScanFileId":1,"modelBimFileId":2,"method":"`+method+`"}`, 10)
			a.fineAlignment(c)
			if w.Code != 200 || !strings.Contains(w.Body.String(), icpFineAlignmentMethod) {
				t.Fatal(w.Code, w.Body.String())
			}
			fineTestNoAudit(t, scan)
		})
	}
}
