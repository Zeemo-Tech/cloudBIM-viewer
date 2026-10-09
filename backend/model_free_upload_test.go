package main

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestPointcloudUploadWithoutMatchingBim(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db, err := gorm.Open(sqlite.Open("file:model_free_upload?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&DBProject{}, &DBAsset{}, &DBUpload{}); err != nil {
		t.Fatal(err)
	}
	project := DBProject{Name: "Lumos", OwnerID: 7}
	if err := db.Create(&project).Error; err != nil {
		t.Fatal(err)
	}
	a := &app{db: db, cfg: config{DataDir: t.TempDir(), UploadFileLimit: 1 << 20}}
	fields := map[string]string{
		"assetType": "pointcloud", "filename": "lumos_1.las",
		"building": "LUMOS", "floor": "1F", "componentType": "YB", "archiveSerial": "1",
		"scanDate": "1790467200",
	}
	// Use the created project ID; no IFC asset exists in this project.
	fields["projectId"] = strconv.FormatInt(project.ID, 10)
	metadata := make([]string, 0, len(fields))
	for key, value := range fields {
		metadata = append(metadata, key+" "+base64.StdEncoding.EncodeToString([]byte(value)))
	}
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Set("userID", int64(7))
	c.Request = httptest.NewRequest(http.MethodPost, "/uploads", nil)
	c.Request.Header.Set("Upload-Length", "100")
	c.Request.Header.Set("Upload-Metadata", strings.Join(metadata, ","))
	a.createUpload(c)
	if c.Writer.Status() != http.StatusCreated {
		t.Fatalf("createUpload status = %d, body = %s", c.Writer.Status(), w.Body.String())
	}
	var upload DBUpload
	if err := db.First(&upload).Error; err != nil {
		t.Fatal(err)
	}
	if upload.LinkedBimID != nil {
		t.Fatalf("independent scan unexpectedly linked to BIM %d", *upload.LinkedBimID)
	}
}
