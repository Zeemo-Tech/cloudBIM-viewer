package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func reportTestToken(t *testing.T, secret string, owner int64) string {
	t.Helper()
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": owner, "exp": time.Now().Add(time.Hour).Unix()})
	value, err := token.SignedString([]byte(secret))
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func validInspectionDiagnostics(t *testing.T, hash string, matrix []float64, tolerance float64) string {
	t.Helper()
	ratio := 1.0
	payload := map[string]any{"rebarComparison": map[string]any{
		"schema": rebarComparisonSchema, "algorithmVersion": rebarC2MAlgorithm, "instanceMapHash": hash,
		"knownVertexCount": 1, "unknownVertexCount": 0,
		"bars": []any{map[string]any{"ifcGlobalId": "ifc-a", "designBarId": "bar-a", "instanceIds": []int{1}, "vertexStart": 0, "vertexCount": 1, "knownCount": 1, "unknownCount": 0, "status": "matched", "stats": &c2mStats{WithinToleranceRatio: 1}}},
		"inspection": rebarInspection{
			Schema: rebarInspectionSchema, CoordinateFrame: "bim", LengthUnit: "m", Method: rebarInspectionMethod,
			Provenance: rebarInspectionProvenance{InstanceMapHash: hash, ControlNetAlgorithmVersion: "control-v4", AlignmentMatrix: matrix},
			Bars: []rebarInspectionBar{{DesignBarID: "bar-a", IFCGlobalID: "ifc-a", Status: "supported", UnitIDs: []string{"unit-a"},
				ObservedSegments: []rebarInspectionObservedSegment{{DesignUnitID: "unit-a", Kind: "body", Evidence: "fitted-control-net", Centerline: [][]float64{{0, 0, 0}, {1, 0, 0}}, SupportedIntervals: [][]float64{{0, 1}}, PointCount: 8, RadiusM: .004, RadiusSource: "design-prior"}},
				KnownVertexCount: 1, ToleranceM: tolerance, WithinToleranceRatio: &ratio,
				Quality: rebarInspectionBarQuality{SupportedUnitCount: 1, DesignUnitCount: 1, Reason: "supported"}}},
			Spacing: []rebarInspectionSpacing{},
			Summary: rebarInspectionSummary{BarCount: 1, SupportedBarCount: 1, ToleranceM: tolerance, ToleranceBasis: "request.toleranceLimit"},
		},
	}}
	encoded, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func TestC2MReportSnapshotIndexesQueryableBarMetrics(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err = db.AutoMigrate(&DBAlignment{}, &DBC2MResult{}, &DBC2MReportRun{}, &DBC2MReportBar{}); err != nil {
		t.Fatal(err)
	}
	a := &app{db: db}
	row := &DBC2MResult{ID: 9, OwnerID: 7, ScanID: 2, BimID: 3, InputFingerprint: "input", ColoredPlyPath: "colored", DistancesPath: "distances", DiagnosticsJSON: `{"rebarComparison":{"bars":[{"ifcGlobalId":"ifc-a","designBarId":"d1","name":"A","status":"matched","knownCount":8,"vertexCount":10,"stats":{"meanAbs":0.1,"p95Abs":0.2,"rmse":0.3},"measurement":{"surface":{"maxAbs":0.4},"bending":{"curvatureMInv":0.5,"maxCentrelineDepartureM":0.6,"residualBowM":0.7}}}]}}`}
	var decoded map[string]any
	if json.Unmarshal([]byte(row.DiagnosticsJSON), &decoded) != nil || decoded["rebarComparison"] == nil {
		t.Fatal("invalid fixture")
	}
	if err := db.Transaction(func(tx *gorm.DB) error { return a.createC2MReportSnapshot(tx, row) }); err != nil {
		t.Fatal(err)
	}
	var count int64
	db.Model(&DBC2MReportBar{}).Count(&count)
	if count != 1 {
		t.Fatalf("bar count = %d", count)
	}
	var bar DBC2MReportBar
	if err := db.Where("ifc_global_id = ? AND status = ? AND max_abs > ?", "ifc-a", "matched", .3).First(&bar).Error; err != nil {
		t.Fatal(err)
	}
	if bar.Coverage == nil || *bar.Coverage != .8 || bar.MaxCentrelineDepartureM == nil || *bar.ResidualBowM != .7 {
		t.Fatalf("stored metrics = %+v", bar)
	}
}

func TestC2MReportDetailIsOwnerScoped(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db, _ := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err := db.AutoMigrate(&DBC2MReportRun{}, &DBC2MReportBar{}); err != nil {
		t.Fatal(err)
	}
	run := DBC2MReportRun{OwnerID: 1, ScanID: 2, BimID: 3, ResultVersion: "secret"}
	if err := db.Create(&run).Error; err != nil {
		t.Fatal(err)
	}
	a := &app{db: db}
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Params = gin.Params{{Key: "version", Value: "secret"}}
	c.Request = httptest.NewRequest(http.MethodGet, "/alignments/bim/c2m/reports/secret", nil)
	c.Set("userID", int64(2))
	a.getC2MReport(c)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("cross-owner report status = %d", recorder.Code)
	}
}

func TestC2MReportSnapshotFailureRollsBackResultAndRun(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&DBC2MResult{}, &DBC2MReportRun{}, &DBC2MReportBar{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Exec(`CREATE TRIGGER reject_report_bar BEFORE INSERT ON dbc2_m_report_bars BEGIN SELECT RAISE(ABORT, 'simulated report failure'); END`).Error; err != nil {
		t.Fatal(err)
	}
	a := &app{db: db}
	row := &DBC2MResult{OwnerID: 1, ScanID: 2, BimID: 3, ColoredPlyPath: "colored", DistancesPath: "distances", DiagnosticsJSON: `{"rebarComparison":{"bars":[{"ifcGlobalId":"A","status":"matched"}]}}`}
	err = db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(row).Error; err != nil {
			return err
		}
		return a.createC2MReportSnapshot(tx, row)
	})
	if err == nil {
		t.Fatal("expected bar insert failure")
	}
	for _, model := range []any{&DBC2MResult{}, &DBC2MReportRun{}, &DBC2MReportBar{}} {
		var count int64
		if err := db.Model(model).Count(&count).Error; err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Fatalf("partial snapshot after rollback: %T = %d", model, count)
		}
	}
}

func TestC2MNormalParametersRejectUnsupportedFallbackAndAngle(t *testing.T) {
	for _, request := range []c2mRequest{
		{NormalConstraintEnabled: true, NormalFallbackMode: "invented"},
		{NormalConstraintEnabled: false, NormalMaxAngleDeg: 120},
	} {
		if normalizeC2MRequest(&request) == nil {
			t.Fatalf("accepted unsupported settings: %+v", request)
		}
	}
}

func TestC2MStandardReportDownloadRequiresAuthAndScopesOwner(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db, _ := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err := db.AutoMigrate(&DBC2MReportRun{}, &DBC2MResult{}); err != nil {
		t.Fatal(err)
	}
	matrix := `[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]`
	diagnostics := validInspectionDiagnostics(t, strings.Repeat("a", 64), []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}, .005)
	run := DBC2MReportRun{OwnerID: 1, ScanID: 2, BimID: 3, ResultVersion: "saved-version", InputFingerprint: strings.Repeat("b", 64), ParamsJSON: `{"tolerance_limit":0.005,"alignmentMatrix":` + matrix + `}`, TransformJSON: matrix, AlgorithmVersion: rebarC2MAlgorithm, Profile: "reference", MetricDirection: "mesh-vertices-to-instance-scan-points", DiagnosticsJSON: diagnostics, CreatedAt: time.Unix(1_700_000_000, 0).UTC()}
	if err := db.Create(&run).Error; err != nil {
		t.Fatal(err)
	}
	secret := "report-test-secret"
	a := &app{db: db, cfg: config{JWTSecret: secret}}
	router := gin.New()
	router.Use(a.authRequired())
	router.GET("/alignments/bim/c2m/reports/:version/json", a.downloadC2MReportJSON)

	request := func(token string) *httptest.ResponseRecorder {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/alignments/bim/c2m/reports/saved-version/json", nil)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		router.ServeHTTP(rec, req)
		return rec
	}
	if got := request(""); got.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated status = %d", got.Code)
	}
	if got := request(reportTestToken(t, secret, 2)); got.Code != http.StatusNotFound {
		t.Fatalf("cross-owner status = %d, body=%s", got.Code, got.Body.String())
	}
	got := request(reportTestToken(t, secret, 1))
	if got.Code != http.StatusOK || !strings.Contains(got.Header().Get("Content-Disposition"), "attachment") {
		t.Fatalf("download status/header = %d %q: %s", got.Code, got.Header().Get("Content-Disposition"), got.Body.String())
	}
	var report c2mStandardReport
	if err := json.Unmarshal(got.Body.Bytes(), &report); err != nil {
		t.Fatal(err)
	}
	if report.Schema != "cloudbim-rebar-inspection-report-v1" || report.Source.ModelScanFileID != 2 || report.Source.ModelBimFileID != 3 || report.ResultVersion != "saved-version" || string(report.AlignmentMatrix) != matrix || string(report.Inspection) == "null" {
		t.Fatalf("standard report lost stored provenance: %+v", report)
	}
}

func TestC2MStandardReportMarksLegacyInspectionUnavailable(t *testing.T) {
	run := DBC2MReportRun{ScanID: 2, BimID: 3, ResultVersion: "legacy", ParamsJSON: `{}`, DiagnosticsJSON: `{"rebarComparison":{"schema":"rebar-comparison-v1"}}`}
	report, err := buildC2MStandardReport(run)
	if err != nil {
		t.Fatal(err)
	}
	if string(report.Inspection) != "null" || report.InspectionUnavailableReason == "" {
		t.Fatalf("legacy inspection availability = %s / %q", report.Inspection, report.InspectionUnavailableReason)
	}
}

func TestC2MStandardReportFallsBackToCurrentPreSnapshotResult(t *testing.T) {
	db, _ := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err := db.AutoMigrate(&DBC2MResult{}, &DBC2MReportRun{}); err != nil {
		t.Fatal(err)
	}
	row := DBC2MResult{ID: 9, OwnerID: 4, ScanID: 2, BimID: 3, InputFingerprint: "input", ParamsJSON: `{}`, DiagnosticsJSON: `{}`, ColoredPlyPath: "colored", DistancesPath: "distances", CreatedAt: time.Now()}
	if err := db.Create(&row).Error; err != nil {
		t.Fatal(err)
	}
	a := &app{db: db}
	run, err := a.findC2MReportRun(4, c2mResultVersion(row))
	if err != nil || run.ScanID != 2 || run.ResultVersion != c2mResultVersion(row) {
		t.Fatalf("current report fallback = %+v, %v", run, err)
	}
	if _, err := a.findC2MReportRun(5, c2mResultVersion(row)); !errors.Is(err, gorm.ErrRecordNotFound) {
		t.Fatalf("foreign current report leaked: %v", err)
	}
}
