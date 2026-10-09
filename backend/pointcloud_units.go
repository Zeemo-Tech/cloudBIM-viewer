package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"strings"
)

// prepareMetricPointcloudSource interprets the explicit Lumos_units LAS VLR.
// Legacy inputs without a recognized declaration retain the metre assumption.
// Millimetre LAS files become immutable, content-addressed metre derivatives;
// integer point records (including colour and extra dimensions) are never edited.
func prepareMetricPointcloudSource(source, dir string) (string, error) {
	f, err := os.Open(source)
	if err != nil {
		return "", err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", err
	}
	prefix, oldPointOffset, err := metricLASPrefix(f, info.Size())
	if err != nil {
		return "", fmt.Errorf("normalize pointcloud units: %w", err)
	}
	if prefix == nil {
		return source, nil
	}
	if _, err = f.Seek(0, io.SeekStart); err != nil {
		return "", err
	}
	hash := sha256.New()
	readSize, err := io.Copy(hash, f)
	if err != nil {
		return "", err
	}
	if readSize != info.Size() {
		return "", errors.New("pointcloud source size changed during unit normalization")
	}
	fingerprint := hash.Sum(nil)
	verifiedPrefix, verifiedOffset, err := metricLASPrefix(f, info.Size())
	if err != nil || verifiedOffset != oldPointOffset || !bytes.Equal(prefix, verifiedPrefix) {
		return "", errors.New("pointcloud source header changed during unit normalization")
	}
	// The version also invalidates artifacts when the normalization algorithm changes.
	output := filepath.Join(dir, "source-meters-v1-"+hex.EncodeToString(fingerprint)+".las")
	if existing, statErr := os.Lstat(output); statErr == nil {
		if !existing.Mode().IsRegular() || existing.Size() != info.Size()+int64(len(prefix))-oldPointOffset {
			return "", fmt.Errorf("invalid cached metric pointcloud %s", output)
		}
		return output, nil
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return "", statErr
	}
	if err = os.MkdirAll(dir, 0755); err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp(dir, ".source-meters-*.las")
	if err != nil {
		return "", err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	if _, err = tmp.Write(prefix); err != nil {
		return "", err
	}
	// Hash the source again while copying to detect mutation between inspection,
	// fingerprinting and publication. A source is never opened for writing.
	if _, err = f.Seek(0, io.SeekStart); err != nil {
		return "", err
	}
	hash.Reset()
	reader := io.TeeReader(f, hash)
	if _, err = io.CopyN(io.Discard, reader, oldPointOffset); err != nil {
		return "", err
	}
	if _, err = io.Copy(tmp, reader); err != nil {
		return "", err
	}
	if !bytes.Equal(fingerprint, hash.Sum(nil)) {
		return "", errors.New("pointcloud source changed during unit normalization")
	}
	if err = tmp.Chmod(0444); err != nil {
		return "", err
	}
	if err = tmp.Sync(); err != nil {
		return "", err
	}
	if err = tmp.Close(); err != nil {
		return "", err
	}
	// Link only the new temporary artifact, never the input. This is an atomic
	// create-if-absent publication, safe across goroutines and backend processes.
	if err = os.Link(tmp.Name(), output); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return "", err
		}
		existing, statErr := os.Lstat(output)
		if statErr != nil || !existing.Mode().IsRegular() || existing.Size() != info.Size()+int64(len(prefix))-oldPointOffset {
			return "", fmt.Errorf("invalid concurrently published metric pointcloud %s", output)
		}
	}
	return output, nil
}

// metricLASPrefix returns a replacement header/VLR block only for explicit mm.
// Waveform formats and compression need changes beyond the XYZ header and are
// deliberately rejected rather than producing a partly converted LAS/LAZ.
func metricLASPrefix(f *os.File, size int64) ([]byte, int64, error) {
	base := make([]byte, 227)
	n, err := f.ReadAt(base, 0)
	if n < 4 || string(base[:4]) != "LASF" {
		return nil, 0, nil
	}
	if err != nil {
		return nil, 0, errors.New("truncated LAS header")
	}
	headerSize := int(binary.LittleEndian.Uint16(base[94:96]))
	pointOffset := int64(binary.LittleEndian.Uint32(base[96:100]))
	if headerSize < len(base) || pointOffset < int64(headerSize) || pointOffset > size || pointOffset > 32<<20 {
		return nil, 0, errors.New("invalid or unsupported LAS header/VLR size")
	}
	prefix := make([]byte, int(pointOffset))
	if _, err := f.ReadAt(prefix, 0); err != nil {
		return nil, 0, err
	}
	vlrCount := binary.LittleEndian.Uint32(base[100:104])
	position := headerSize
	var replacement bytes.Buffer
	replacement.Write(prefix[:headerSize])
	factor := 0.0
	for i := uint32(0); i < vlrCount; i++ {
		if position+54 > len(prefix) {
			return nil, 0, errors.New("truncated LAS VLR header")
		}
		header := bytes.Clone(prefix[position : position+54])
		length := int(binary.LittleEndian.Uint16(header[20:22]))
		position += 54
		if length > len(prefix)-position {
			return nil, 0, errors.New("truncated LAS VLR payload")
		}
		payload := prefix[position : position+length]
		if lasUnitVLR(header) {
			updated, unitFactor, err := metricUnitMetadata(payload)
			if err != nil {
				return nil, 0, err
			}
			if unitFactor != 0 {
				if factor != 0 && factor != unitFactor {
					return nil, 0, errors.New("conflicting LAS unit declarations")
				}
				factor = unitFactor
			}
			if updated != nil {
				payload = updated
				if len(payload) > math.MaxUint16 {
					return nil, 0, errors.New("normalized LAS unit VLR is too large")
				}
				binary.LittleEndian.PutUint16(header[20:22], uint16(len(payload)))
			}
		}
		replacement.Write(header)
		replacement.Write(payload)
		position += length
	}
	if factor != 0.001 {
		return nil, 0, nil
	}
	replacement.Write(prefix[position:]) // Preserve padding and other prefix data.
	out := replacement.Bytes()
	minor := base[25]
	format := base[104]
	minimumHeader := map[byte]int{2: 227, 3: 235, 4: 375}[minor]
	if base[24] != 1 || minimumHeader == 0 || headerSize < minimumHeader {
		return nil, 0, errors.New("explicit millimetre conversion supports LAS 1.2–1.4 only")
	}
	if format&0xc0 != 0 || format > 8 || format == 4 || format == 5 || (minor < 4 && format > 3) {
		return nil, 0, errors.New("explicit millimetre conversion requires uncompressed LAS without waveform points")
	}
	if minor >= 3 && binary.LittleEndian.Uint64(prefix[227:235]) != 0 {
		return nil, 0, errors.New("millimetre LAS with waveform data is unsupported")
	}
	recordSize := uint64(binary.LittleEndian.Uint16(base[105:107]))
	minimumRecord := map[byte]uint64{0: 20, 1: 28, 2: 26, 3: 34, 6: 30, 7: 36, 8: 38}[format]
	pointCount := uint64(binary.LittleEndian.Uint32(base[107:111]))
	if minor == 4 {
		pointCount = binary.LittleEndian.Uint64(prefix[247:255])
	}
	if recordSize < minimumRecord || pointCount > uint64(size-pointOffset)/recordSize {
		return nil, 0, errors.New("invalid or truncated LAS point data")
	}
	delta := int64(len(out)) - pointOffset
	if int64(len(out)) > math.MaxUint32 {
		return nil, 0, errors.New("normalized LAS point offset overflow")
	}
	binary.LittleEndian.PutUint32(out[96:100], uint32(len(out)))
	if minor == 4 {
		evlrOffset := binary.LittleEndian.Uint64(prefix[235:243])
		evlrCount := binary.LittleEndian.Uint32(prefix[243:247])
		if evlrOffset != 0 {
			if evlrOffset < uint64(pointOffset)+pointCount*recordSize || evlrOffset > uint64(size) {
				return nil, 0, errors.New("invalid LAS EVLR offset")
			}
			binary.LittleEndian.PutUint64(out[235:243], uint64(int64(evlrOffset)+delta))
		}
		if evlrCount > 0 {
			if evlrOffset == 0 {
				return nil, 0, errors.New("missing LAS EVLR offset")
			}
			if err := checkMetricLASEVLRs(f, evlrOffset, evlrCount, uint64(size)); err != nil {
				return nil, 0, err
			}
		}
	}
	for offset := 131; offset < 227; offset += 8 {
		value := math.Float64frombits(binary.LittleEndian.Uint64(out[offset : offset+8]))
		if math.IsNaN(value) || math.IsInf(value, 0) || (offset < 155 && value <= 0) {
			return nil, 0, errors.New("invalid LAS coordinate scale, offset or bounds")
		}
		converted := value * factor
		if offset < 155 && converted == 0 {
			return nil, 0, errors.New("LAS coordinate scale underflows during metre conversion")
		}
		binary.LittleEndian.PutUint64(out[offset:offset+8], math.Float64bits(converted))
	}
	return out, pointOffset, nil
}

func lasUnitVLR(header []byte) bool {
	return strings.TrimRight(string(header[2:18]), "\x00 ") == "Lumos_units" && binary.LittleEndian.Uint16(header[18:20]) == 1
}

func metricUnitMetadata(payload []byte) ([]byte, float64, error) {
	var metadata map[string]json.RawMessage
	if err := json.Unmarshal(bytes.TrimRight(payload, "\x00"), &metadata); err != nil || metadata == nil {
		return nil, 0, errors.New("invalid Lumos_units JSON declaration")
	}
	factor := 0.0
	for _, key := range []string{"units", "unit_symbol"} {
		var value string
		if data, ok := metadata[key]; ok {
			if err := json.Unmarshal(data, &value); err != nil {
				return nil, 0, fmt.Errorf("invalid Lumos_units %s", key)
			}
		}
		candidate := 0.0
		switch strings.ToLower(strings.TrimSpace(value)) {
		case "mm", "millimeter", "millimeters", "millimetre", "millimetres":
			candidate = 0.001
		case "m", "meter", "meters", "metre", "metres":
			candidate = 1
		}
		if candidate != 0 {
			if factor != 0 && factor != candidate {
				return nil, 0, errors.New("conflicting Lumos_units names")
			}
			factor = candidate
		}
	}
	if data, ok := metadata["meters_per_unit"]; ok && factor != 0 {
		var declared float64
		if err := json.Unmarshal(data, &declared); err != nil || declared != factor {
			return nil, 0, errors.New("conflicting Lumos_units meters_per_unit")
		}
	}
	if factor != 0.001 {
		return nil, factor, nil
	}
	original, err := json.Marshal(metadata)
	if err != nil {
		return nil, 0, err
	}
	metadata["cloudbim_original_unit_metadata"] = original
	metadata["units"] = json.RawMessage(`"meters"`)
	metadata["unit_symbol"] = json.RawMessage(`"m"`)
	metadata["meters_per_unit"] = json.RawMessage(`1`)
	metadata["cloudbim_unit_normalization"] = json.RawMessage(`{"version":1,"coordinate_scale_applied":0.001,"point_records_preserved":true}`)
	updated, err := json.Marshal(metadata)
	return updated, factor, err
}

func checkMetricLASEVLRs(f *os.File, offset uint64, count uint32, size uint64) error {
	header := make([]byte, 60)
	for i := uint32(0); i < count; i++ {
		if offset > size || size-offset < 60 {
			return errors.New("truncated LAS EVLR header")
		}
		if _, err := f.ReadAt(header, int64(offset)); err != nil {
			return err
		}
		length := binary.LittleEndian.Uint64(header[20:28])
		if length > size-offset-60 {
			return errors.New("truncated LAS EVLR payload")
		}
		if lasUnitVLR(header) {
			return errors.New("Lumos_units in extended VLRs is unsupported for millimetre conversion")
		}
		offset += 60 + length
	}
	return nil
}
