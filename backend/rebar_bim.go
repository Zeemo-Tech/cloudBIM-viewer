package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
)

type rebarBimSelection struct {
	BimAssetID int64 `json:"bimAssetId"`
}

// Resolve every input from owner-scoped database records, never client paths.
func (a *app) resolveRebarBimPrior(scanID, ownerID int64, selection *rebarBimSelection) (*RebarBimPrior, error) {
	return a.resolveBimPrior(scanID, ownerID, selection, true)
}

// Polling and tile requests use file identity; avoid hashing entire models on every read.
func (a *app) resolveBimPrior(scanID, ownerID int64, selection *rebarBimSelection, hashContents bool) (*RebarBimPrior, error) {
	if selection == nil {
		return nil, nil
	}
	var bim DBAsset
	if selection.BimAssetID <= 0 || a.db.Where("id=? AND owner_id=? AND type=? AND status=?", selection.BimAssetID, ownerID, "bim", "ready").First(&bim).Error != nil {
		return nil, errors.New("BIM asset unavailable")
	}
	var alignment DBAlignment
	if a.db.Where("scan_id=? AND bim_id=? AND owner_id=?", scanID, bim.ID, ownerID).First(&alignment).Error != nil {
		return nil, errors.New("saved BIM alignment required")
	}
	prior := &RebarBimPrior{}
	if json.Unmarshal([]byte(alignment.MatrixJSON), &prior.ScanToBim) != nil || len(prior.ScanToBim) != 16 {
		return nil, errors.New("invalid alignment")
	}
	for _, v := range prior.ScanToBim {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, errors.New("invalid alignment")
		}
	}
	hashInputs := []string{"rebar-bim-prior-v1", alignment.MatrixJSON}
	input := func(path string) (string, error) {
		rel, err := filepath.Rel(a.cfg.DataDir, path)
		if err != nil {
			return "", err
		}
		confined, err := rebarFile(a.cfg.DataDir, rel)
		if err != nil {
			return "", err
		}
		if hashContents {
			data, err := os.ReadFile(confined)
			if err != nil {
				return "", err
			}
			hashInputs = append(hashInputs, hashBytes(data))
		} else {
			info, err := os.Stat(confined)
			if err != nil {
				return "", err
			}
			hashInputs = append(hashInputs, fmt.Sprintf("%s:%d:%d", confined, info.Size(), info.ModTime().UnixNano()))
		}
		return meshServicePath(a.cfg.DataDir, confined, a.cfg.MeshServiceStorageDir), nil
	}
	var err error
	prior.ModelPath, err = input(filepath.Join(bim.Dir, "model.glb"))
	if err != nil {
		return nil, err
	}
	prior.MetadataPath, err = input(filepath.Join(bim.Dir, "metadata.json"))
	if err != nil {
		return nil, err
	}
	var uploads []DBUpload
	if err = a.db.Where("asset_id=? AND owner_id=? AND status=?", bim.ID, ownerID, "ready").Order("created_at DESC, id DESC").Find(&uploads).Error; err != nil {
		return nil, err
	}
	for _, upload := range uploads {
		path := filepath.Join(upload.Dir, "source")
		if _, statErr := os.Stat(path); statErr != nil {
			path = filepath.Join(a.cfg.DataDir, "uploads", upload.ID, "source")
		}
		if _, statErr := os.Stat(path); statErr != nil {
			continue
		}
		prior.IFCPath, err = input(path)
		if err != nil {
			return nil, err
		}
		break
	}
	encoded, _ := canonicalJSON(hashInputs)
	prior.Fingerprint = hashBytes([]byte(encoded))
	return prior, nil
}
