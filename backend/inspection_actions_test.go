package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func newInspectionActionTestApp(t *testing.T) (*app, DBC2MReportRun) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	db, err := gorm.Open(sqlite.Open(filepath.Join(t.TempDir(), "actions.sqlite")), &gorm.Config{TranslateError: true})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = sqlDB.Close() })
	if err := db.AutoMigrate(&DBUser{}, &DBC2MReportRun{}, &DBC2MReportBar{}, &DBInspectionAction{}); err != nil {
		t.Fatal(err)
	}
	run := DBC2MReportRun{OwnerID: 1, ScanID: 2, BimID: 3, ResultVersion: "report-actions-v1", DiagnosticsJSON: `{"immutable":true}`}
	if err := db.Create(&run).Error; err != nil {
		t.Fatal(err)
	}
	for _, bar := range []DBC2MReportBar{
		{RunID: run.ID, IFCGlobalID: "ifc-a", Status: "matched", RawJSON: `{"status":"matched"}`},
		{RunID: run.ID, IFCGlobalID: "ifc-missing", Status: "missing", RawJSON: `{"status":"missing"}`},
	} {
		if err := db.Create(&bar).Error; err != nil {
			t.Fatal(err)
		}
	}
	return &app{db: db, cfg: config{JWTSecret: "actions-test-secret"}}, run
}

func inspectionActionTestRequest(a *app, owner int64, version, method, body string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Set("userID", owner)
	c.Params = gin.Params{{Key: "version", Value: version}}
	c.Request = httptest.NewRequest(method, "/alignments/bim/c2m/reports/"+version+"/actions", strings.NewReader(body))
	c.Request.Header.Set("Content-Type", "application/json")
	if method == http.MethodGet {
		a.listInspectionActions(c)
	} else {
		a.createInspectionAction(c)
	}
	return rec
}

func inspectionActionBody(bar, action, note string, demonstration bool) string {
	encoded, _ := json.Marshal(inspectionActionRequest{IFCGlobalID: bar, Action: action, Note: note, Demonstration: demonstration})
	return string(encoded)
}

func TestInspectionActionsOwnerScopeAndUnknownBar(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		rec := inspectionActionTestRequest(a, 2, run.ResultVersion, method, inspectionActionBody("ifc-a", "acknowledge", "", false))
		if rec.Code != http.StatusNotFound {
			t.Fatalf("cross-owner %s: status=%d body=%s", method, rec.Code, rec.Body.String())
		}
	}
	for _, pair := range [][2]string{{"unknown-report", "ifc-a"}, {run.ResultVersion, "unknown-bar"}} {
		rec := inspectionActionTestRequest(a, 1, pair[0], http.MethodPost, inspectionActionBody(pair[1], "acknowledge", "", false))
		if rec.Code != http.StatusNotFound {
			t.Fatalf("unknown report/bar: status=%d body=%s", rec.Code, rec.Body.String())
		}
	}
	var count int64
	if err := a.db.Model(&DBInspectionAction{}).Count(&count).Error; err != nil || count != 0 {
		t.Fatalf("unauthorized events written: count=%d err=%v", count, err)
	}
}

func TestInspectionActionsRequireAuthentication(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	router := gin.New()
	router.Use(a.authRequired())
	router.GET("/alignments/bim/c2m/reports/:version/actions", a.listInspectionActions)
	router.POST("/alignments/bim/c2m/reports/:version/actions", a.createInspectionAction)
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(method, "/alignments/bim/c2m/reports/"+run.ResultVersion+"/actions", strings.NewReader(inspectionActionBody("ifc-a", "acknowledge", "", false)))
		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("unauthenticated %s status=%d", method, rec.Code)
		}
	}
}

func TestInspectionActionsRejectIllegalTransitionsAndForgedCompletion(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	for _, test := range []struct {
		body   string
		status int
	}{
		{inspectionActionBody("ifc-a", "record_adjustment", "调整保护层", false), http.StatusConflict},
		{inspectionActionBody("ifc-a", "request_recheck", "", false), http.StatusConflict},
		{inspectionActionBody("ifc-a", "complete", "", false), http.StatusBadRequest},
		{inspectionActionBody("ifc-a", "pass", "", false), http.StatusBadRequest},
		{`{"ifcGlobalId":"ifc-a","action":"acknowledge","passed":true}`, http.StatusBadRequest},
		{`{"ifcGlobalId":"ifc-a","action":"acknowledge","status":"completed"}`, http.StatusBadRequest},
		{`{"ifcGlobalId":"ifc-a","action":"acknowledge"} {}`, http.StatusBadRequest},
	} {
		rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, test.body)
		if rec.Code != test.status {
			t.Fatalf("body=%s status=%d want=%d response=%s", test.body, rec.Code, test.status, rec.Body.String())
		}
	}
	rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-a", "acknowledge", "", false))
	if rec.Code != http.StatusCreated {
		t.Fatalf("acknowledge status=%d body=%s", rec.Code, rec.Body.String())
	}
	for _, test := range []struct {
		body   string
		status int
	}{
		{inspectionActionBody("ifc-a", "acknowledge", "", false), http.StatusConflict},
		{inspectionActionBody("ifc-a", "request_recheck", "", false), http.StatusConflict},
		{inspectionActionBody("ifc-a", "record_adjustment", " \n\t", false), http.StatusBadRequest},
	} {
		rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, test.body)
		if rec.Code != test.status {
			t.Fatalf("body=%s status=%d want=%d", test.body, rec.Code, test.status)
		}
	}
}

func TestInspectionActionsNormalChainPersistsWithoutChangingReport(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	steps := []string{"acknowledge", "record_adjustment", "request_recheck"}
	for _, step := range steps {
		rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-missing", step, "安排补扫后复检", false))
		if rec.Code != http.StatusCreated {
			t.Fatalf("missing-bar %s status=%d body=%s", step, rec.Code, rec.Body.String())
		}
	}
	// A fresh application instance must read events from the database.
	fresh := &app{db: a.db}
	rec := inspectionActionTestRequest(fresh, 1, run.ResultVersion, http.MethodGet, "")
	var result struct {
		Code int `json:"code"`
		Data struct {
			Items []DBInspectionAction `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if rec.Code != http.StatusOK || result.Code != 200 || len(result.Data.Items) != 3 {
		t.Fatalf("persisted list=%s", rec.Body.String())
	}
	for i, event := range result.Data.Items {
		if event.ID <= 0 || event.CreatedAt.IsZero() || event.Action != steps[i] || event.ResultVersion != run.ResultVersion || event.IFCGlobalID != "ifc-missing" || event.Demonstration {
			t.Fatalf("event[%d]=%+v", i, event)
		}
	}
	for _, step := range steps {
		rec = inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-missing", step, "再登记", false))
		if rec.Code != http.StatusConflict {
			t.Fatalf("duplicate final chain %s status=%d", step, rec.Code)
		}
	}
	var saved DBC2MReportRun
	if err := a.db.First(&saved, run.ID).Error; err != nil || saved.DiagnosticsJSON != run.DiagnosticsJSON {
		t.Fatalf("report mutated=%+v err=%v", saved, err)
	}
	var bar DBC2MReportBar
	if err := a.db.Where("run_id = ? AND ifc_global_id = ?", run.ID, "ifc-missing").First(&bar).Error; err != nil || bar.Status != "missing" || bar.RawJSON != `{"status":"missing"}` {
		t.Fatalf("bar acceptance mutated=%+v err=%v", bar, err)
	}
}

func TestInspectionActionsDemonstrationIsIndependent(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	empty := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodGet, "")
	if empty.Code != http.StatusOK || !strings.Contains(empty.Body.String(), `"items":[]`) {
		t.Fatalf("empty list=%s", empty.Body.String())
	}
	for _, step := range []string{"acknowledge", "record_adjustment", "request_recheck"} {
		rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-a", step, "演示记录", true))
		if rec.Code != http.StatusCreated {
			t.Fatalf("demo %s status=%d body=%s", step, rec.Code, rec.Body.String())
		}
	}
	rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-a", "record_adjustment", "真实调整", false))
	if rec.Code != http.StatusConflict {
		t.Fatalf("demo advanced real chain: status=%d", rec.Code)
	}
	for _, step := range []string{"acknowledge", "record_adjustment", "request_recheck"} {
		rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-a", step, "真实登记", false))
		if rec.Code != http.StatusCreated {
			t.Fatalf("real %s status=%d body=%s", step, rec.Code, rec.Body.String())
		}
	}
	for _, demonstration := range []bool{true, false} {
		var count int64
		if err := a.db.Model(&DBInspectionAction{}).Where("demonstration = ?", demonstration).Count(&count).Error; err != nil || count != 3 {
			t.Fatalf("demonstration=%v count=%d err=%v", demonstration, count, err)
		}
	}
	rec = inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodGet, "")
	var result struct {
		Data struct {
			Items []DBInspectionAction `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &result); err != nil || len(result.Data.Items) != 6 {
		t.Fatalf("combined demo/real list=%s err=%v", rec.Body.String(), err)
	}
	for i, event := range result.Data.Items {
		if event.Demonstration != (i < 3) {
			t.Fatalf("demo flag missing or changed: event[%d]=%+v", i, event)
		}
	}
}

func TestInspectionActionsConcurrentDuplicateIsRejected(t *testing.T) {
	a, run := newInspectionActionTestApp(t)
	for _, step := range []string{"acknowledge", "record_adjustment", "request_recheck"} {
		statuses := make(chan int, 12)
		var workers sync.WaitGroup
		for i := 0; i < 12; i++ {
			workers.Add(1)
			go func() {
				defer workers.Done()
				rec := inspectionActionTestRequest(a, 1, run.ResultVersion, http.MethodPost, inspectionActionBody("ifc-a", step, "并发登记", false))
				statuses <- rec.Code
			}()
		}
		workers.Wait()
		close(statuses)
		successes := 0
		for status := range statuses {
			if status == http.StatusCreated {
				successes++
			} else if status != http.StatusConflict {
				t.Fatalf("concurrent %s unexpected status=%d", step, status)
			}
		}
		if successes != 1 {
			t.Fatalf("concurrent %s created %d events", step, successes)
		}
	}
	var count int64
	if err := a.db.Model(&DBInspectionAction{}).Count(&count).Error; err != nil || count != 3 {
		t.Fatalf("concurrent event count=%d err=%v", count, err)
	}
}
