package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	mysqlDriver "github.com/go-sql-driver/mysql"
	"github.com/golang-jwt/jwt/v5"
	"github.com/joho/godotenv"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/driver/mysql"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

type response struct {
	Code int    `json:"code"`
	Msg  string `json:"msg"`
	Data any    `json:"data,omitempty"`
}

func ok(c *gin.Context, data any) {
	c.JSON(http.StatusOK, response{Code: 200, Msg: "success", Data: data})
}
func created(c *gin.Context, data any) {
	c.JSON(http.StatusCreated, response{Code: 201, Msg: "success", Data: data})
}
func fail(c *gin.Context, code int, msg string) { c.JSON(code, response{Code: code, Msg: msg}) }

type User struct {
	ID           int64     `json:"id"`
	Username     string    `json:"username"`
	PasswordHash string    `json:"-"`
	CreatedAt    time.Time `json:"createdAt"`
}
type Asset struct {
	ID                 int64              `json:"id"`
	Type               string             `json:"type"`
	SourceName         string             `json:"sourceName"`
	SourceSize         int64              `json:"sourceSize"`
	Status             string             `json:"status"`
	ErrorMessage       *string            `json:"errorMessage"`
	CreatedAt          int64              `json:"createdAt"`
	OwnerID            int64              `json:"-"`
	Dir                string             `json:"-"`
	PointcloudColor    string             `json:"pointcloudColor,omitempty"`
	MeshRemesh         *MeshRemeshSummary `json:"meshRemesh,omitempty"`
	RemeshStatus       string             `json:"-"`
	RemeshError        *string            `json:"-"`
	RemeshQueuedAt     *time.Time         `json:"-"`
	RemeshStartedAt    *time.Time         `json:"-"`
	RemeshFinishedAt   *time.Time         `json:"-"`
	RemeshVertexBefore int                `json:"-"`
	RemeshFaceBefore   int                `json:"-"`
	RemeshVertexAfter  int                `json:"-"`
	RemeshFaceAfter    int                `json:"-"`
}
type MeshRemeshSummary struct {
	Supported      bool             `json:"supported"`
	Status         string           `json:"status,omitempty"`
	CanManualRetry bool             `json:"canManualRetry"`
	ResultFileID   *int64           `json:"resultFileId,omitempty"`
	LastError      *string          `json:"lastError,omitempty"`
	QueuedAt       *time.Time       `json:"queuedAt,omitempty"`
	StartedAt      *time.Time       `json:"startedAt,omitempty"`
	FinishedAt     *time.Time       `json:"finishedAt,omitempty"`
	Stats          *meshRemeshStats `json:"stats,omitempty"`
}
type Upload struct {
	ID           string  `json:"uploadId"`
	AssetID      int64   `json:"assetId"`
	AssetType    string  `json:"assetType"`
	FileName     string  `json:"fileName"`
	FileSize     int64   `json:"fileSize"`
	Offset       int64   `json:"uploadOffset"`
	Status       string  `json:"status"`
	ErrorMessage *string `json:"errorMessage"`
	OwnerID      int64   `json:"-"`
	Dir          string  `json:"-"`
}
type Alignment struct {
	ModelID              int64     `json:"modelId"`
	ScanID               int64     `json:"modelScanFileId"`
	BimID                int64     `json:"modelBimFileId"`
	ModelBIMBuildingName *string   `json:"modelBimBuildingName,omitempty"`
	Qx                   float64   `json:"modelRotationQx"`
	Qy                   float64   `json:"modelRotationQy"`
	Qz                   float64   `json:"modelRotationQz"`
	Qw                   float64   `json:"modelRotationQw"`
	Tx                   float64   `json:"modelTranslationX"`
	Ty                   float64   `json:"modelTranslationY"`
	Tz                   float64   `json:"modelTranslationZ"`
	Matrix               []float64 `json:"modelMatrix"`
	RMSE                 float64   `json:"modelRmse"`
	MaxError             float64   `json:"modelMaxError"`
	PairCount            int       `json:"modelPairCount"`
	InlierCount          int       `json:"modelInlierCount"`
}
type DBUser struct {
	ID           int64  `gorm:"primaryKey"`
	Username     string `gorm:"size:128;uniqueIndex;not null"`
	PasswordHash string `gorm:"size:255;not null"`
	CreatedAt    time.Time
}
type DBAsset struct {
	ID                 int64   `gorm:"primaryKey"`
	Type               string  `gorm:"size:32;index;not null"`
	SourceName         string  `gorm:"size:255;not null"`
	SourceSize         int64   `gorm:"not null"`
	Status             string  `gorm:"size:32;index;not null"`
	ErrorMessage       *string `gorm:"type:text"`
	CreatedAt          int64   `gorm:"index;not null"`
	OwnerID            int64   `gorm:"index;not null"`
	Dir                string  `gorm:"size:1024;not null"`
	PointcloudColor    string  `gorm:"column:pointcloud_color;size:7"`
	RemeshStatus       string  `gorm:"size:32;index"`
	RemeshError        *string `gorm:"type:text"`
	RemeshAlgorithm    string  `gorm:"size:64"`
	RemeshParamsJSON   string  `gorm:"type:text"`
	RemeshQueuedAt     *time.Time
	RemeshStartedAt    *time.Time
	RemeshFinishedAt   *time.Time
	RemeshVertexBefore int
	RemeshFaceBefore   int
	RemeshVertexAfter  int
	RemeshFaceAfter    int
}
type DBAssetDerivative struct {
	ID           int64   `gorm:"primaryKey"`
	AssetID      int64   `gorm:"uniqueIndex:idx_asset_derivative_kind;index;not null"`
	Kind         string  `gorm:"size:64;uniqueIndex:idx_asset_derivative_kind;not null"`
	Format       string  `gorm:"size:32;not null"`
	Status       string  `gorm:"size:32;index;not null"`
	RelativePath string  `gorm:"size:1024"`
	EntryPath    string  `gorm:"size:1024"`
	Version      string  `gorm:"size:128"`
	ContentHash  string  `gorm:"size:128"`
	ByteSize     int64   `gorm:"not null;default:0"`
	ParamsJSON   string  `gorm:"type:text"`
	ErrorMessage *string `gorm:"type:text"`
	CreatedAt    time.Time
	UpdatedAt    time.Time
}
type DBUpload struct {
	ID           string `gorm:"primaryKey;size:64"`
	AssetID      int64
	AssetType    string  `gorm:"size:32;not null"`
	FileName     string  `gorm:"size:255;not null"`
	FileSize     int64   `gorm:"not null"`
	Offset       int64   `gorm:"not null"`
	Status       string  `gorm:"size:32;index;not null"`
	ErrorMessage *string `gorm:"type:text"`
	OwnerID      int64   `gorm:"index;not null"`
	Dir          string  `gorm:"size:1024;not null"`
	CreatedAt    time.Time
}
type DBAlignment struct {
	ID                     int64 `gorm:"primaryKey"`
	ScanID                 int64 `gorm:"uniqueIndex:idx_scan_bim;not null"`
	BimID                  int64 `gorm:"uniqueIndex:idx_scan_bim;not null"`
	Qx, Qy, Qz, Qw         float64
	Tx, Ty, Tz             float64
	MatrixJSON             string `gorm:"type:text"`
	RMSE, MaxError         float64
	PairCount, InlierCount int
	OwnerID                int64 `gorm:"index;not null"`
	CreatedAt              time.Time
}

type DBMeasurement struct {
	ID        int64     `gorm:"primaryKey" json:"id"`
	AssetID   int64     `gorm:"index;not null" json:"assetId"`
	OwnerID   int64     `gorm:"index;not null" json:"-"`
	Kind      string    `gorm:"size:32;not null" json:"kind"`
	Payload   string    `gorm:"type:text;not null" json:"payload"`
	CreatedAt time.Time `json:"createdAt"`
}

type DBC2MResult struct {
	ID                   int64 `gorm:"primaryKey"`
	ScanID               int64 `gorm:"uniqueIndex:idx_c2m_scan_bim;not null"`
	BimID                int64 `gorm:"uniqueIndex:idx_c2m_scan_bim;not null"`
	OwnerID              int64 `gorm:"index;not null"`
	PointsBefore         int
	PointsAfter          int
	MeshVertexCount      int
	VoxelSize            float64
	MaxColormapDistance  float64
	MaxHistogramDistance float64
	HistogramBins        int
	ToleranceLimit       float64
	InputFingerprint     string `gorm:"size:64;index"`
	ParamsJSON           string `gorm:"type:text"`
	MinDist              float64
	MeanDist             float64
	StdDist              float64
	P50                  float64
	P90                  float64
	P95                  float64
	P99                  float64
	MaxDist              float64
	MeanAbs              float64
	RMSE                 float64
	P95Abs               float64
	WithinToleranceRatio float64
	Profile              string `gorm:"size:32"`
	AlgorithmVersion     string `gorm:"size:128"`
	MetricDirection      string `gorm:"size:128"`
	ApproximationJSON    string `gorm:"type:text"`
	HistogramJSON        string `gorm:"type:text"`
	DiagnosticsJSON      string `gorm:"type:text"`
	ColoredPlyPath       string `gorm:"size:2048"`
	DistancesPath        string `gorm:"size:2048"`
	CreatedAt            time.Time
	UpdatedAt            time.Time
}

type AssetRepresentation struct {
	Kind         string  `json:"kind"`
	Format       string  `json:"format"`
	Status       string  `json:"status"`
	Version      string  `json:"version,omitempty"`
	ByteSize     int64   `json:"byteSize,omitempty"`
	ContentHash  string  `json:"contentHash,omitempty"`
	URL          string  `json:"url,omitempty"`
	MetadataURL  string  `json:"metadataUrl,omitempty"`
	BaseURL      string  `json:"baseUrl,omitempty"`
	ErrorMessage *string `json:"errorMessage,omitempty"`
	Legacy       bool    `json:"legacy,omitempty"`
}

type config struct {
	Addr                  string
	DataDir               string
	JWTSecret             string
	JWTExpiresIn          time.Duration
	MeshServiceURL        string
	MeshServiceStorageDir string
	RegisterCode          string
	Environment           string
	DBDriver              string
	DBDSN                 string
	CORSAllowOrigins      []string
	SeedDemo              bool
	WorkerCount           int
	UploadChunkLimit      int64
	UploadFileLimit       int64
	PointcloudSubsample   float64
	ShutdownTimeout       time.Duration
}

const developmentJWTSecret = "cloudbim-dev-secret"

func loadConfig() (config, error) {
	environment := strings.ToLower(env("APP_ENV", "development"))
	secret := env("JWT_SECRET", developmentJWTSecret)
	if environment == "production" && (secret == developmentJWTSecret || len(secret) < 32) {
		return config{}, errors.New("生产环境必须设置长度至少 32 位的 JWT_SECRET")
	}
	defaultJWTExpiresIn := "720h"
	if environment == "production" {
		defaultJWTExpiresIn = "24h"
	}
	jwtExpiresIn, err := parseJWTDuration(env("JWT_EXPIRES_IN", defaultJWTExpiresIn))
	if err != nil || jwtExpiresIn <= 0 {
		return config{}, errors.New("JWT_EXPIRES_IN 必须是大于 0 的 duration，例如 720h 或 30d")
	}
	if environment == "production" {
		if strings.TrimSpace(os.Getenv("DB_PASSWORD")) == "" {
			return config{}, errors.New("生产环境必须设置 DB_PASSWORD")
		}
		if strings.TrimSpace(os.Getenv("CORS_ALLOW_ORIGINS")) == "" {
			return config{}, errors.New("生产环境必须设置 CORS_ALLOW_ORIGINS")
		}
		if strings.TrimSpace(os.Getenv("REGISTER_CODE")) == "" {
			return config{}, errors.New("生产环境必须设置 REGISTER_CODE")
		}
	}
	dbDriver := strings.ToLower(env("DB_DRIVER", "postgres"))
	if dbDriver != "postgres" && dbDriver != "mysql" {
		return config{}, fmt.Errorf("DB_DRIVER 必须是 postgres 或 mysql，当前为 %q", dbDriver)
	}
	dbHost := env("DB_HOST", "127.0.0.1")
	defaultPort := "5432"
	defaultUser := "postgres"
	defaultPassword := "AuS4tpYfwNSu"
	if dbDriver == "mysql" {
		defaultPort, defaultUser, defaultPassword = "3306", "root", ""
	}
	dbPort := env("DB_PORT", defaultPort)
	dbUser := env("DB_USER", defaultUser)
	dbPassword := env("DB_PASSWORD", defaultPassword)
	dbName := env("DB_NAME", "cloudbim")
	var dsn string
	if dbDriver == "mysql" {
		// parseTime is required for GORM time.Time fields; utf8mb4 preserves metadata safely.
		dsn = (&mysqlDriver.Config{
			User:                 dbUser,
			Passwd:               dbPassword,
			Net:                  "tcp",
			Addr:                 dbHost + ":" + dbPort,
			DBName:               dbName,
			ParseTime:            true,
			Loc:                  time.Local,
			AllowNativePasswords: true,
			Params:               map[string]string{"charset": "utf8mb4"},
		}).FormatDSN()
	} else {
		dsn = fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=%s TimeZone=Asia/Shanghai", dbHost, dbPort, dbUser, dbPassword, dbName, env("DB_SSLMODE", "disable"))
	}
	root, err := filepath.Abs(env("CLOUDBIM_DATA_DIR", filepath.Join(".", "data")))
	if err != nil {
		return config{}, fmt.Errorf("解析数据目录失败: %w", err)
	}
	origins := []string{}
	for _, origin := range strings.Split(env("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"), ",") {
		if value := strings.TrimSpace(origin); value != "" {
			origins = append(origins, value)
		}
	}
	seedDemo := env("CLOUDBIM_SEED_DEMO", "true") == "true"
	if environment == "production" && os.Getenv("CLOUDBIM_SEED_DEMO") == "" {
		seedDemo = false
	}
	workers, _ := strconv.Atoi(env("PROCESSING_WORKERS", "2"))
	if workers < 1 || workers > 32 {
		workers = 2
	}
	chunkLimit, _ := strconv.ParseInt(env("UPLOAD_CHUNK_LIMIT", strconv.FormatInt(64*1024*1024, 10)), 10, 64)
	if chunkLimit < 1*1024*1024 {
		chunkLimit = 64 * 1024 * 1024
	}
	fileLimit, _ := strconv.ParseInt(env("UPLOAD_FILE_LIMIT", strconv.FormatInt(100*1024*1024*1024, 10)), 10, 64)
	if fileLimit < chunkLimit {
		fileLimit = 100 * 1024 * 1024 * 1024
	}
	pointcloudSubsample, err := strconv.ParseFloat(env("POINTCLOUD_SUBSAMPLE", "0.25"), 64)
	if err != nil || pointcloudSubsample < 0.01 || pointcloudSubsample > 1 {
		return config{}, errors.New("POINTCLOUD_SUBSAMPLE 必须是 0.01 到 1 之间的小数")
	}
	shutdownSeconds, _ := strconv.Atoi(env("SHUTDOWN_TIMEOUT_SECONDS", "15"))
	if shutdownSeconds < 1 || shutdownSeconds > 300 {
		shutdownSeconds = 15
	}
	meshServiceStorageDir := strings.TrimSpace(env("MESH_SERVICE_STORAGE_DIR", "/storage"))
	if meshServiceStorageDir == "" || !filepath.IsAbs(meshServiceStorageDir) {
		return config{}, errors.New("MESH_SERVICE_STORAGE_DIR 必须是绝对路径")
	}
	return config{Addr: env("ADDR", ":8090"), DataDir: root, JWTSecret: secret, JWTExpiresIn: jwtExpiresIn, MeshServiceURL: strings.TrimRight(env("MESH_SERVICE_URL", "http://127.0.0.1:8001"), "/"), MeshServiceStorageDir: filepath.Clean(meshServiceStorageDir), RegisterCode: env("REGISTER_CODE", "laochen"), Environment: environment, DBDriver: dbDriver, DBDSN: dsn, CORSAllowOrigins: origins, SeedDemo: seedDemo, WorkerCount: workers, UploadChunkLimit: chunkLimit, UploadFileLimit: fileLimit, PointcloudSubsample: pointcloudSubsample, ShutdownTimeout: time.Duration(shutdownSeconds) * time.Second}, nil
}

func parseJWTDuration(value string) (time.Duration, error) {
	value = strings.TrimSpace(value)
	if strings.HasSuffix(strings.ToLower(value), "d") {
		days, err := strconv.ParseFloat(strings.TrimSpace(value[:len(value)-1]), 64)
		if err != nil {
			return 0, err
		}
		return time.Duration(days * float64(24*time.Hour)), nil
	}
	return time.ParseDuration(value)
}

type app struct {
	mu                sync.RWMutex
	uploadMu          sync.Mutex
	c2mMutationMu     sync.Mutex
	c2mOperationLocks sync.Map
	db                *gorm.DB
	cfg               config
	jobs              chan string
	remeshJobs        chan int64
	workerWG          sync.WaitGroup
}

func newApp(cfg config) *app {
	return &app{
		cfg:        cfg,
		jobs:       make(chan string, cfg.WorkerCount*4),
		remeshJobs: make(chan int64, cfg.WorkerCount*4),
	}
}
func env(k, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return fallback
}
func (a *app) connectDB() error {
	var dialector gorm.Dialector
	switch a.cfg.DBDriver {
	case "mysql":
		dialector = mysql.Open(a.cfg.DBDSN)
	case "postgres":
		dialector = postgres.Open(a.cfg.DBDSN)
	default:
		return fmt.Errorf("不支持的数据库驱动: %s", a.cfg.DBDriver)
	}
	db, err := gorm.Open(dialector, &gorm.Config{TranslateError: true})
	if err != nil {
		return fmt.Errorf("连接 %s 失败: %w", a.cfg.DBDriver, err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		return fmt.Errorf("获取数据库连接池失败: %w", err)
	}
	sqlDB.SetMaxOpenConns(25)
	sqlDB.SetMaxIdleConns(10)
	sqlDB.SetConnMaxLifetime(30 * time.Minute)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := sqlDB.PingContext(ctx); err != nil {
		return fmt.Errorf("%s 数据库不可用: %w", a.cfg.DBDriver, err)
	}
	if err := db.AutoMigrate(&DBUser{}, &DBAsset{}, &DBAssetDerivative{}, &DBUpload{}, &DBAlignment{}, &DBC2MResult{}, &DBMeasurement{}); err != nil {
		return fmt.Errorf("数据库迁移失败: %w", err)
	}
	a.db = db
	if a.cfg.SeedDemo {
		var count int64
		if db.Model(&DBUser{}).Where("username = ?", "demo").Count(&count).Error == nil && count == 0 {
			hash, hashErr := bcrypt.GenerateFromPassword([]byte("demo123456"), bcrypt.DefaultCost)
			if hashErr != nil {
				return fmt.Errorf("生成演示账号密码失败: %w", hashErr)
			}
			if err := db.Create(&DBUser{Username: "demo", PasswordHash: string(hash), CreatedAt: time.Now()}).Error; err != nil {
				return fmt.Errorf("创建演示账号失败: %w", err)
			}
		}
	}
	if err := a.reconcileReadyAssets(); err != nil {
		return err
	}
	return nil
}

// reconcileReadyAssets prevents artifacts left by old demo builds from being
// exposed as production-ready assets after upgrading the service.
func (a *app) reconcileReadyAssets() error {
	var assets []DBAsset
	if err := a.db.Where("status = ?", "ready").Find(&assets).Error; err != nil {
		return fmt.Errorf("校验资产产物失败: %w", err)
	}
	for _, asset := range assets {
		if validAssetArtifacts(asset) {
			continue
		}
		message := "转换产物缺失或无效，请重新上传原始文件"
		if err := a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{"status": "failed", "error_message": message}).Error; err != nil {
			return fmt.Errorf("更新无效资产 %d 状态失败: %w", asset.ID, err)
		}
	}
	return nil
}

func validAssetArtifacts(asset DBAsset) bool {
	if asset.Type == "bim" {
		modelPath := filepath.Join(asset.Dir, "model.glb")
		metadataPath := filepath.Join(asset.Dir, "metadata.json")
		model, err := os.ReadFile(modelPath)
		if err != nil || len(model) < 32 || bytes.Contains(model, []byte("cloudBIM-viewer backend")) {
			return false
		}
		metadata, err := os.ReadFile(metadataPath)
		if err != nil || !json.Valid(metadata) || bytes.Contains(metadata, []byte(`"source":"source"`)) {
			return false
		}
		return true
	}
	if asset.Type == "pointcloud" {
		tilesetPath := filepath.Join(asset.Dir, "tiles", "tileset.json")
		if info, err := os.Stat(tilesetPath); err != nil || info.Size() < 256 {
			return false
		}
		var tileset map[string]any
		content, err := os.ReadFile(tilesetPath)
		if err != nil || json.Unmarshal(content, &tileset) != nil {
			return false
		}
		return hasTileContent(asset.Dir, tileset)
	}
	return false
}

func hasTileContent(assetDir string, tileset map[string]any) bool {
	root, ok := tileset["root"].(map[string]any)
	if !ok {
		return false
	}
	return hasTileContentNode(filepath.Join(assetDir, "tiles"), root, make(map[string]bool), 0)
}

// hasTileContentNode validates at least one reachable payload. gocesiumtiler
// emits nested tilesets and small edge tiles, so byte-size thresholds are not
// valid; validate the standard PNTS header and section lengths instead.
func hasTileContentNode(tilesDir string, node map[string]any, visited map[string]bool, depth int) bool {
	if depth > 64 {
		return false
	}
	if content, ok := node["content"].(map[string]any); ok {
		uri := tileURI(content)
		if uri != "" {
			path, err := safeJoin(tilesDir, uri)
			if err == nil {
				ext := strings.ToLower(filepath.Ext(path))
				switch ext {
				case ".pnts":
					if validPNTSFile(path) {
						return true
					}
				case ".json":
					if !visited[path] {
						visited[path] = true
						if child, err := readTileset(path); err == nil && hasTileContentNode(filepath.Dir(path), child, visited, depth+1) {
							return true
						}
					}
				}
			}
		}
	}
	if children, ok := node["children"].([]any); ok {
		for _, item := range children {
			if child, ok := item.(map[string]any); ok && hasTileContentNode(tilesDir, child, visited, depth+1) {
				return true
			}
		}
	}
	return false
}

func tileURI(content map[string]any) string {
	for _, key := range []string{"uri", "url"} {
		if value, ok := content[key].(string); ok && strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func readTileset(path string) (map[string]any, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var tileset map[string]any
	if err := json.Unmarshal(content, &tileset); err != nil {
		return nil, err
	}
	return tileset, nil
}

func validPNTSFile(path string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	header := make([]byte, 28)
	if _, err := io.ReadFull(file, header); err != nil || string(header[:4]) != "pnts" || binary.LittleEndian.Uint32(header[4:8]) != 1 {
		return false
	}
	info, err := file.Stat()
	if err != nil || info.Size() < 28 {
		return false
	}
	declaredLength := int64(binary.LittleEndian.Uint32(header[8:12]))
	if declaredLength < 28 || declaredLength > info.Size() {
		return false
	}
	featureJSONLength := int64(binary.LittleEndian.Uint32(header[12:16]))
	featureBinaryLength := int64(binary.LittleEndian.Uint32(header[16:20]))
	batchJSONLength := int64(binary.LittleEndian.Uint32(header[20:24]))
	batchBinaryLength := int64(binary.LittleEndian.Uint32(header[24:28]))
	sectionsLength := int64(28) + featureJSONLength + featureBinaryLength + batchJSONLength + batchBinaryLength
	if sectionsLength != info.Size() {
		return false
	}
	featureJSON := make([]byte, int(featureJSONLength))
	if _, err := io.ReadFull(file, featureJSON); err != nil {
		return false
	}
	var featureTable map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(featureJSON), &featureTable); err != nil {
		return false
	}
	points, ok := featureTable["POINTS_LENGTH"].(float64)
	return ok && points > 0 && points == math.Trunc(points)
}
func randomID() string { var b [12]byte; _, _ = rand.Read(b[:]); return hex.EncodeToString(b[:]) }

func (a *app) authRequired() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.Method == http.MethodOptions || c.Request.URL.Path == "/health" || (c.Request.Method == http.MethodPost && (c.Request.URL.Path == "/auth/login" || c.Request.URL.Path == "/auth/register")) {
			c.Next()
			return
		}
		h := c.GetHeader("Authorization")
		if !strings.HasPrefix(h, "Bearer ") {
			fail(c, 401, "缺少或非法的 Authorization 头")
			c.Abort()
			return
		}
		token, err := jwt.Parse(strings.TrimSpace(strings.TrimPrefix(h, "Bearer ")), func(t *jwt.Token) (any, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, errors.New("invalid signing method")
			}
			return []byte(a.cfg.JWTSecret), nil
		})
		if err != nil || !token.Valid {
			fail(c, 401, "token 无效或已过期")
			c.Abort()
			return
		}
		claims, okClaims := token.Claims.(jwt.MapClaims)
		if !okClaims {
			fail(c, 401, "token 无效")
			c.Abort()
			return
		}
		id, _ := strconv.ParseInt(fmt.Sprint(claims["sub"]), 10, 64)
		if id == 0 {
			fail(c, 401, "token 无效")
			c.Abort()
			return
		}
		c.Set("userID", id)
		c.Next()
	}
}
func userID(c *gin.Context) int64 { v, _ := c.Get("userID"); id, _ := v.(int64); return id }

func (a *app) register(c *gin.Context) {
	var req struct {
		Username     string `json:"username"`
		Password     string `json:"password"`
		RegisterCode string `json:"registerCode"`
	}
	if c.ShouldBindJSON(&req) != nil || strings.TrimSpace(req.Username) == "" || len(req.Password) < 6 {
		fail(c, 400, "用户名和密码格式不正确")
		return
	}
	if strings.TrimSpace(req.RegisterCode) != a.cfg.RegisterCode {
		fail(c, 400, "注册码无效")
		return
	}
	name := strings.TrimSpace(req.Username)
	a.mu.Lock()
	defer a.mu.Unlock()
	var existing DBUser
	if err := a.db.Where("lower(username) = lower(?)", name).First(&existing).Error; err == nil {
		fail(c, 409, "用户名已存在")
		return
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, 500, "查询用户失败")
		return
	}
	hash, hashErr := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if hashErr != nil {
		fail(c, 500, "生成密码摘要失败")
		return
	}
	u := DBUser{Username: name, PasswordHash: string(hash), CreatedAt: time.Now()}
	if err := a.db.Create(&u).Error; err != nil {
		if errors.Is(err, gorm.ErrDuplicatedKey) {
			fail(c, 409, "用户名已存在")
			return
		}
		fail(c, 500, "保存用户失败")
		return
	}
	created(c, User{ID: u.ID, Username: u.Username, CreatedAt: u.CreatedAt})
}
func (a *app) login(c *gin.Context) {
	var req struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if c.ShouldBindJSON(&req) != nil {
		fail(c, 400, "登录参数不完整")
		return
	}
	var found DBUser
	if err := a.db.Where("lower(username) = lower(?)", strings.TrimSpace(req.Username)).First(&found).Error; err != nil || bcrypt.CompareHashAndPassword([]byte(found.PasswordHash), []byte(req.Password)) != nil {
		fail(c, 401, "用户名或密码错误")
		return
	}
	now := time.Now()
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": strconv.FormatInt(found.ID, 10), "username": found.Username, "exp": now.Add(a.cfg.JWTExpiresIn).Unix(), "iat": now.Unix()})
	signed, err := token.SignedString([]byte(a.cfg.JWTSecret))
	if err != nil {
		fail(c, 500, "生成 token 失败")
		return
	}
	ok(c, gin.H{"token": signed})
}
func (a *app) me(c *gin.Context) {
	var u DBUser
	if err := a.db.First(&u, userID(c)).Error; err != nil {
		fail(c, 404, "用户不存在")
		return
	}
	ok(c, gin.H{"id": u.ID, "username": u.Username})
}

func decodeTusMetadata(value string) map[string]string {
	result := map[string]string{}
	for _, item := range strings.Split(value, ",") {
		p := strings.SplitN(strings.TrimSpace(item), " ", 2)
		if len(p) != 2 {
			continue
		}
		b, err := decodeBase64(p[1])
		if err == nil {
			result[p[0]] = string(b)
		}
	}
	return result
}
func decodeBase64(v string) ([]byte, error) {
	return base64.StdEncoding.DecodeString(v)
}
func validType(t string) bool { return t == "bim" || t == "pointcloud" }
func validExtension(typ, name string) bool {
	ext := strings.ToLower(filepath.Ext(name))
	return (typ == "bim" && ext == ".ifc") || (typ == "pointcloud" && ext == ".las")
}
func (a *app) createUpload(c *gin.Context) {
	length, err := strconv.ParseInt(c.GetHeader("Upload-Length"), 10, 64)
	if err != nil || length <= 0 {
		fail(c, 400, "Upload-Length 非法")
		return
	}
	if length > a.cfg.UploadFileLimit {
		fail(c, 413, "文件超过服务器允许的最大大小")
		return
	}
	meta := decodeTusMetadata(c.GetHeader("Upload-Metadata"))
	typ, name := meta["assetType"], filepath.Base(meta["filename"])
	if !validType(typ) || name == "." || name == "" || len(name) > 255 || strings.IndexByte(name, 0) >= 0 || !validExtension(typ, name) {
		fail(c, 400, "文件名或资产类型非法")
		return
	}
	id := randomID()
	dir := filepath.Join(a.cfg.DataDir, "uploads", id)
	if err := os.MkdirAll(dir, 0755); err != nil {
		fail(c, 500, "创建上传目录失败")
		return
	}
	up := DBUpload{ID: id, AssetType: typ, FileName: name, FileSize: length, Status: "uploading", OwnerID: userID(c), Dir: dir, CreatedAt: time.Now()}
	if err := a.db.Create(&up).Error; err != nil {
		fail(c, 500, "创建上传会话失败")
		return
	}
	c.Header("Location", "/uploads/"+id)
	c.Header("Upload-Offset", "0")
	c.Header("Upload-Length", strconv.FormatInt(length, 10))
	c.Header("Tus-Resumable", "1.0.0")
	c.Status(http.StatusCreated)
}
func (a *app) findUpload(id string) (*DBUpload, error) {
	var up DBUpload
	if err := a.db.First(&up, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &up, nil
}
func (a *app) upload(c *gin.Context) {
	id := c.Param("id")
	up, err := a.findUpload(id)
	if err != nil || up.OwnerID != userID(c) {
		fail(c, 404, "上传会话不存在")
		return
	}
	a.uploadMu.Lock()
	defer a.uploadMu.Unlock()
	// Re-read after acquiring the lock so concurrent PATCH requests cannot
	// append data using a stale offset.
	up, err = a.findUpload(id)
	if err != nil || up.OwnerID != userID(c) {
		fail(c, 404, "上传会话不存在")
		return
	}
	if c.Request.Method == http.MethodHead {
		c.Header("Upload-Length", strconv.FormatInt(up.FileSize, 10))
		c.Header("Upload-Offset", strconv.FormatInt(up.Offset, 10))
		c.Header("Tus-Resumable", "1.0.0")
		c.Status(204)
		return
	}
	if c.Request.Method == http.MethodDelete {
		if up.Status != "uploading" {
			fail(c, 409, "当前上传状态不可终止")
			return
		}
		if err := a.db.Model(&DBUpload{}).Where("id = ? AND owner_id = ?", up.ID, userID(c)).Updates(map[string]any{"status": "terminated"}).Error; err != nil {
			fail(c, 500, "终止上传失败")
			return
		}
		_ = os.RemoveAll(up.Dir)
		c.Status(204)
		return
	}
	if up.Status != "uploading" {
		fail(c, 409, "当前上传状态不接受分片")
		return
	}
	expected, _ := strconv.ParseInt(c.GetHeader("Upload-Offset"), 10, 64)
	if c.GetHeader("Upload-Offset") == "" || expected < 0 {
		fail(c, 400, "Upload-Offset 非法")
		return
	}
	if expected != up.Offset {
		fail(c, 409, "上传偏移不一致")
		return
	}
	remaining := up.FileSize - up.Offset
	if remaining < 0 {
		fail(c, 409, "上传状态非法")
		return
	}
	if c.Request.ContentLength > remaining {
		fail(c, 413, "上传分片超过剩余大小")
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, minInt64(a.cfg.UploadChunkLimit, remaining))
	f, err := os.OpenFile(filepath.Join(up.Dir, "source"), os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		fail(c, 500, "打开上传文件失败")
		return
	}
	if _, err = f.Seek(up.Offset, io.SeekStart); err == nil {
		_, err = io.Copy(f, c.Request.Body)
	}
	_ = f.Close()
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			fail(c, http.StatusRequestEntityTooLarge, "上传分片超过服务器限制")
			return
		}
		fail(c, 500, "写入上传分片失败")
		return
	}
	offset, err := fileSize(filepath.Join(up.Dir, "source"))
	if err != nil {
		fail(c, 500, "读取上传进度失败")
		return
	}
	if offset > up.FileSize {
		fail(c, 413, "上传文件超过声明大小")
		return
	}
	status := up.Status
	if offset >= up.FileSize {
		status = "queued"
	}
	if err := a.db.Model(up).Updates(map[string]any{"offset": offset, "status": status}).Error; err != nil {
		fail(c, 500, "保存上传进度失败")
		return
	}
	next := *up
	next.Offset, next.Status = offset, status
	c.Header("Upload-Length", strconv.FormatInt(next.FileSize, 10))
	c.Header("Upload-Offset", strconv.FormatInt(next.Offset, 10))
	c.Header("Tus-Resumable", "1.0.0")
	if next.Status == "queued" {
		a.enqueue(next.ID)
	}
	c.Status(204)
}
func minInt64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
func fileSize(path string) (int64, error) {
	s, err := os.Stat(path)
	if err != nil {
		return 0, err
	}
	return s.Size(), nil
}

func safeJoin(base, relative string) (string, error) {
	baseAbs, err := filepath.Abs(base)
	if err != nil {
		return "", err
	}
	candidate := filepath.Join(baseAbs, filepath.Clean(relative))
	candidateAbs, err := filepath.Abs(candidate)
	if err != nil {
		return "", err
	}
	prefix := baseAbs + string(os.PathSeparator)
	if candidateAbs != baseAbs && !strings.HasPrefix(candidateAbs, prefix) {
		return "", errors.New("非法资源路径")
	}
	return candidateAbs, nil
}
func (a *app) uploadStatus(c *gin.Context) {
	up, err := a.findUpload(c.Param("id"))
	if err != nil || up.OwnerID != userID(c) {
		fail(c, 404, "上传会话不存在")
		return
	}
	status := up.Status
	var errorMessage = up.ErrorMessage
	assetID := any(nil)
	if up.AssetID > 0 {
		assetID = up.AssetID
		if status == "ready" {
			var asset DBAsset
			if err := a.db.Where("id = ? AND owner_id = ?", up.AssetID, userID(c)).First(&asset).Error; err != nil {
				// Do not advertise a ready upload whose asset can no longer be
				// fetched by this user; this prevents a guaranteed /assets/:id 404.
				status = "failed"
				message := "上传已完成，但资产记录不存在，请重新上传"
				errorMessage = &message
				assetID = nil
				log.Printf("上传 %s 引用的资产 %d 不存在或无权访问", up.ID, up.AssetID)
			}
		}
	}
	result := gin.H{"uploadId": up.ID, "assetId": assetID, "assetType": up.AssetType, "fileName": up.FileName, "fileSize": up.FileSize, "uploadOffset": up.Offset, "uploadLength": up.FileSize, "status": status, "errorMessage": errorMessage}
	ok(c, result)
}

func (a *app) processUpload(ctx context.Context, uploadID string) {
	up, err := a.findUpload(uploadID)
	if err != nil {
		return
	}
	claimed := a.db.Model(&DBUpload{}).Where("id = ? AND status = ?", uploadID, "queued").Update("status", "processing")
	if claimed.Error != nil || claimed.RowsAffected != 1 {
		return
	}
	source := filepath.Join(up.Dir, "source")
	if info, statErr := os.Stat(source); statErr != nil || info.Size() != up.FileSize {
		msg := "上传源文件不存在或大小与声明不一致"
		if statErr != nil {
			msg = fmt.Sprintf("读取上传源文件失败: %v", statErr)
		}
		_ = a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		return
	}
	asset := DBAsset{Type: up.AssetType, SourceName: up.FileName, SourceSize: up.FileSize, Status: "processing", CreatedAt: time.Now().Unix(), OwnerID: up.OwnerID, Dir: filepath.Join(a.cfg.DataDir, "assets", randomID())}
	if err := a.db.Create(&asset).Error; err != nil {
		msg := err.Error()
		_ = a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		return
	}
	if err := a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"asset_id": asset.ID}).Error; err != nil {
		msg := fmt.Sprintf("保存资产关联失败: %v", err)
		_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		_ = a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		return
	}
	if err := os.MkdirAll(asset.Dir, 0755); err != nil {
		msg := fmt.Sprintf("创建资产目录失败: %v", err)
		_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		_ = a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error
		return
	}
	if asset.Type == "bim" {
		err = buildBIM(ctx, source, asset.Dir)
	} else {
		err = buildPointCloud(ctx, source, asset.Dir, a.cfg.PointcloudSubsample)
	}
	if err == nil && !validAssetArtifacts(asset) {
		err = errors.New("转换完成但产物缺失或无效")
	}
	if err != nil {
		msg := err.Error()
		if updateErr := a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error; updateErr != nil {
			log.Printf("更新失败资产 %d 状态失败: %v", asset.ID, updateErr)
		}
		if updateErr := a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Updates(map[string]any{"status": "failed", "error_message": msg}).Error; updateErr != nil {
			log.Printf("更新上传 %s 状态失败: %v", uploadID, updateErr)
		}
	} else {
		if asset.Type == "pointcloud" {
			assetSource := filepath.Join(asset.Dir, "source.las")
			_ = linkOrSymlink(source, assetSource)
		}
		if updateErr := a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Update("status", "ready").Error; updateErr != nil {
			log.Printf("更新资产 %d 就绪状态失败: %v", asset.ID, updateErr)
		}
		if updateErr := a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Update("status", "ready").Error; updateErr != nil {
			log.Printf("更新上传 %s 就绪状态失败: %v", uploadID, updateErr)
		}
		if asset.Type == "bim" {
			if queueErr := a.queueRemeshAsset(asset.ID, "bim_preprocessor", defaultRemeshParamsJSON, false); queueErr != nil {
				log.Printf("BIM 资产 %d 自动网格均匀化入队失败: %v", asset.ID, queueErr)
			}
		}
	}
}

func (a *app) enqueue(uploadID string) {
	a.jobs <- uploadID
}

func (a *app) enqueueRemesh(assetID int64) {
	a.remeshJobs <- assetID
}

func (a *app) startWorkers(ctx context.Context) error {
	for i := 0; i < a.cfg.WorkerCount; i++ {
		a.workerWG.Add(1)
		go func() {
			defer a.workerWG.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case id := <-a.jobs:
					a.processUpload(ctx, id)
				}
			}
		}()
	}
	a.workerWG.Add(1)
	go func() {
		defer a.workerWG.Done()
		for {
			select {
			case <-ctx.Done():
				return
			case assetID := <-a.remeshJobs:
				a.processRemeshJob(ctx, assetID)
			}
		}
	}()
	if err := a.db.Model(&DBUpload{}).Where("status = ?", "processing").Update("status", "queued").Error; err != nil {
		return fmt.Errorf("恢复未完成上传任务失败: %w", err)
	}
	var pending []DBUpload
	if err := a.db.Where("status = ?", "queued").Order("created_at ASC").Find(&pending).Error; err != nil {
		return fmt.Errorf("读取待处理任务失败: %w", err)
	}
	for _, up := range pending {
		a.enqueue(up.ID)
	}
	return a.recoverRemeshJobs()
}

func (a *app) waitWorkers() {
	a.workerWG.Wait()
}
func meshRemeshSummary(a Asset) *MeshRemeshSummary {
	if a.Type != "bim" {
		return &MeshRemeshSummary{Supported: false}
	}
	status := a.RemeshStatus
	if status == "" {
		if _, err := os.Stat(filepath.Join(a.Dir, "mesh_remesh.ply")); err == nil {
			status = "succeeded"
		} else {
			status = "idle"
		}
	} else if status == "succeeded" {
		if _, err := os.Stat(filepath.Join(a.Dir, "mesh_remesh.ply")); err != nil {
			status = "failed"
		}
	}
	canRetry := status == "failed" || status == "idle"
	resultID := (*int64)(nil)
	var stats *meshRemeshStats
	if status == "succeeded" {
		resultID = &a.ID
		if a.RemeshVertexBefore > 0 || a.RemeshFaceBefore > 0 || a.RemeshVertexAfter > 0 || a.RemeshFaceAfter > 0 {
			stats = &meshRemeshStats{
				VertexBefore: a.RemeshVertexBefore,
				FaceBefore:   a.RemeshFaceBefore,
				VertexAfter:  a.RemeshVertexAfter,
				FaceAfter:    a.RemeshFaceAfter,
			}
		}
	}
	return &MeshRemeshSummary{
		Supported:      true,
		Status:         status,
		CanManualRetry: canRetry,
		ResultFileID:   resultID,
		LastError:      a.RemeshError,
		QueuedAt:       a.RemeshQueuedAt,
		StartedAt:      a.RemeshStartedAt,
		FinishedAt:     a.RemeshFinishedAt,
		Stats:          stats,
	}
}

func assetFromDB(item DBAsset) Asset {
	pointcloudColor := strings.ToLower(strings.TrimSpace(item.PointcloudColor))
	return Asset{
		ID:                 item.ID,
		Type:               item.Type,
		SourceName:         item.SourceName,
		SourceSize:         item.SourceSize,
		Status:             item.Status,
		ErrorMessage:       item.ErrorMessage,
		CreatedAt:          item.CreatedAt,
		OwnerID:            item.OwnerID,
		Dir:                item.Dir,
		PointcloudColor:    pointcloudColor,
		RemeshStatus:       item.RemeshStatus,
		RemeshError:        item.RemeshError,
		RemeshQueuedAt:     item.RemeshQueuedAt,
		RemeshStartedAt:    item.RemeshStartedAt,
		RemeshFinishedAt:   item.RemeshFinishedAt,
		RemeshVertexBefore: item.RemeshVertexBefore,
		RemeshFaceBefore:   item.RemeshFaceBefore,
		RemeshVertexAfter:  item.RemeshVertexAfter,
		RemeshFaceAfter:    item.RemeshFaceAfter,
	}
}

func assetSummary(a Asset) gin.H {
	result := gin.H{"id": a.ID, "type": a.Type, "sourceName": a.SourceName, "sourceSize": a.SourceSize, "status": a.Status, "errorMessage": a.ErrorMessage, "createdAt": a.CreatedAt, "meshRemesh": meshRemeshSummary(a)}
	if a.Type == "pointcloud" {
		result["pointcloudColor"] = a.PointcloudColor
	}
	return result
}

var pointcloudColorPattern = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)

func (a *app) updateAssetAppearance(c *gin.Context) {
	asset, found := a.getAsset(c)
	if !found {
		fail(c, http.StatusNotFound, "资产不存在")
		return
	}
	if asset.Type != "pointcloud" {
		fail(c, http.StatusBadRequest, "仅点云资产支持渲染设置")
		return
	}
	var req struct {
		PointcloudColor string `json:"pointcloudColor"`
	}
	if c.ShouldBindJSON(&req) != nil {
		fail(c, http.StatusBadRequest, "渲染设置格式非法")
		return
	}
	colorValue := strings.TrimSpace(req.PointcloudColor)
	if colorValue == "" {
		if err := a.db.Model(&DBAsset{}).
			Where("id = ? AND owner_id = ?", asset.ID, userID(c)).
			Update("pointcloud_color", "").Error; err != nil {
			fail(c, http.StatusInternalServerError, "恢复点云原始颜色失败")
			return
		}
		ok(c, gin.H{"pointcloudColor": nil})
		return
	}
	if !pointcloudColorPattern.MatchString(colorValue) {
		fail(c, http.StatusBadRequest, "点云颜色必须是 #RRGGBB 格式")
		return
	}
	colorValue = strings.ToLower(colorValue)
	if err := a.db.Model(&DBAsset{}).
		Where("id = ? AND owner_id = ?", asset.ID, userID(c)).
		Update("pointcloud_color", colorValue).Error; err != nil {
		fail(c, http.StatusInternalServerError, "保存点云颜色失败")
		return
	}
	ok(c, gin.H{"pointcloudColor": colorValue})
}
func (a *app) listAssets(c *gin.Context) {
	typ, status := c.Query("type"), c.Query("status")
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	size, _ := strconv.Atoi(c.DefaultQuery("pageSize", "100"))
	if page < 1 {
		page = 1
	}
	if size < 1 || size > 500 {
		size = 100
	}
	var rows []DBAsset
	query := a.db.Where("owner_id = ?", userID(c))
	if typ != "" {
		query = query.Where("type = ?", typ)
	}
	if status != "" {
		query = query.Where("status = ?", status)
	}
	var total int64
	if err := query.Model(&DBAsset{}).Count(&total).Error; err != nil {
		fail(c, 500, "统计资产失败")
		return
	}
	// Keep the result set bounded even for large workspaces.
	if err := query.Order("created_at DESC").Limit(size).Offset((page - 1) * size).Find(&rows).Error; err != nil {
		fail(c, 500, "查询资产失败")
		return
	}
	list := []gin.H{}
	for _, item := range rows {
		list = append(list, assetSummary(assetFromDB(item)))
	}
	ok(c, gin.H{"total": total, "page": page, "pageSize": size, "list": list})
}
func (a *app) getAsset(c *gin.Context) (*Asset, bool) {
	id, _ := strconv.ParseInt(c.Param("id"), 10, 64)
	var row DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", id, userID(c)).First(&row).Error; err != nil {
		return nil, false
	}
	item := assetFromDB(row)
	return &item, true
}
func (a *app) assetDetail(c *gin.Context) {
	item, found := a.getAsset(c)
	if !found {
		fail(c, 404, "资产不存在")
		return
	}
	data := assetSummary(*item)
	if item.Status == "ready" {
		if item.Type == "bim" {
			data["glbUrl"] = fmt.Sprintf("/assets/%d/glb", item.ID)
			data["metadataUrl"] = fmt.Sprintf("/assets/%d/metadata", item.ID)
		} else {
			data["tilesBaseUrl"] = fmt.Sprintf("/assets/%d/tiles/", item.ID)
			data["tilesetUrl"] = fmt.Sprintf("/assets/%d/tiles/tileset.json", item.ID)
		}
	}
	ok(c, data)
}
func (a *app) deleteAsset(c *gin.Context) {
	item, found := a.getAsset(c)
	if !found {
		fail(c, 404, "资产不存在")
		return
	}
	var c2mRows []DBC2MResult
	if err := a.db.Where("owner_id = ? AND (scan_id = ? OR bim_id = ?)", userID(c), item.ID, item.ID).Find(&c2mRows).Error; err != nil {
		fail(c, 500, "查询关联 C2M 结果失败")
		return
	}
	if err := a.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Delete(&DBAssetDerivative{}, "asset_id = ?", item.ID).Error; err != nil {
			return err
		}
		if err := tx.Delete(&DBAlignment{}, "owner_id = ? AND (scan_id = ? OR bim_id = ?)", userID(c), item.ID, item.ID).Error; err != nil {
			return err
		}
		if err := tx.Delete(&DBC2MResult{}, "owner_id = ? AND (scan_id = ? OR bim_id = ?)", userID(c), item.ID, item.ID).Error; err != nil {
			return err
		}
		if err := tx.Delete(&DBMeasurement{}, "owner_id = ? AND asset_id = ?", userID(c), item.ID).Error; err != nil {
			return err
		}
		return tx.Delete(&DBAsset{}, "id = ? AND owner_id = ?", item.ID, userID(c)).Error
	}); err != nil {
		fail(c, 500, "删除资产失败")
		return
	}
	for _, row := range c2mRows {
		a.removeC2MArtifact(row.ColoredPlyPath)
		a.removeC2MArtifact(row.DistancesPath)
	}
	_ = os.RemoveAll(item.Dir)
	ok(c, nil)
}

func escapedRelativeURLPath(path string) string {
	parts := strings.Split(filepath.ToSlash(filepath.Clean(path)), "/")
	for index, part := range parts {
		parts[index] = url.PathEscape(part)
	}
	return strings.Join(parts, "/")
}

func derivativeResourcePath(asset Asset, row DBAssetDerivative, relative string) (string, error) {
	if strings.TrimSpace(row.Kind) == "" || strings.TrimSpace(row.Version) == "" || strings.TrimSpace(row.RelativePath) == "" || strings.TrimSpace(row.EntryPath) == "" {
		return "", errors.New("派生物资源信息不完整")
	}
	if filepath.IsAbs(row.RelativePath) || filepath.IsAbs(row.EntryPath) || filepath.Clean(row.EntryPath) == "." {
		return "", errors.New("派生物资源路径非法")
	}
	root, err := safeJoin(asset.Dir, row.RelativePath)
	if err != nil {
		return "", err
	}
	if _, err := safeJoin(root, row.EntryPath); err != nil {
		return "", err
	}
	return safeJoin(root, relative)
}

func representationFromDerivative(asset Asset, row DBAssetDerivative) AssetRepresentation {
	representation := AssetRepresentation{
		Kind:         row.Kind,
		Format:       row.Format,
		Status:       row.Status,
		Version:      row.Version,
		ByteSize:     row.ByteSize,
		ContentHash:  row.ContentHash,
		ErrorMessage: row.ErrorMessage,
	}
	if row.Status == "ready" {
		if _, err := derivativeResourcePath(asset, row, row.EntryPath); err == nil {
			baseURL := fmt.Sprintf("/assets/%d/representations/%s/%s/", asset.ID, url.PathEscape(row.Kind), url.PathEscape(row.Version))
			representation.BaseURL = baseURL
			representation.URL = baseURL + escapedRelativeURLPath(row.EntryPath)
		}
	}
	return representation
}

func legacyAssetRepresentations(asset Asset) []AssetRepresentation {
	if asset.Type == "bim" {
		browse := AssetRepresentation{
			Kind:        "browse-detail",
			Format:      "glb",
			Status:      asset.Status,
			URL:         fmt.Sprintf("/assets/%d/glb", asset.ID),
			MetadataURL: fmt.Sprintf("/assets/%d/metadata", asset.ID),
			Legacy:      true,
		}
		source := AssetRepresentation{Kind: "source", Format: "ifc", Status: asset.Status, ByteSize: asset.SourceSize, Legacy: true}
		remesh := meshRemeshSummary(asset)
		compute := AssetRepresentation{Kind: "compute-mesh", Format: "ply", Status: remesh.Status, ErrorMessage: remesh.LastError, Legacy: true}
		if remesh.Status == "succeeded" {
			compute.URL = fmt.Sprintf("/assets/%d/mesh/remesh/latest", asset.ID)
		}
		return []AssetRepresentation{browse, source, compute}
	}
	if asset.Type == "pointcloud" {
		return []AssetRepresentation{
			{
				Kind:    "browse-detail",
				Format:  "3d-tiles",
				Status:  asset.Status,
				URL:     fmt.Sprintf("/assets/%d/tiles/tileset.json", asset.ID),
				BaseURL: fmt.Sprintf("/assets/%d/tiles/", asset.ID),
				Legacy:  true,
			},
			{Kind: "source", Format: "las", Status: asset.Status, ByteSize: asset.SourceSize, Legacy: true},
		}
	}
	return nil
}

func mergeAssetRepresentations(asset Asset, rows []DBAssetDerivative) []AssetRepresentation {
	representations := legacyAssetRepresentations(asset)
	indices := make(map[string]int, len(representations))
	for index, representation := range representations {
		indices[representation.Kind] = index
	}
	for _, row := range rows {
		representation := representationFromDerivative(asset, row)
		if index, exists := indices[representation.Kind]; exists {
			representations[index] = representation
			continue
		}
		indices[representation.Kind] = len(representations)
		representations = append(representations, representation)
	}
	return representations
}

func (a *app) assetRepresentations(c *gin.Context) {
	item, found := a.getAsset(c)
	if !found {
		fail(c, http.StatusNotFound, "资产不存在")
		return
	}
	var rows []DBAssetDerivative
	if err := a.db.Where("asset_id = ?", item.ID).Order("kind ASC").Find(&rows).Error; err != nil {
		fail(c, http.StatusInternalServerError, "查询资产派生物失败")
		return
	}
	representations := mergeAssetRepresentations(*item, rows)
	ok(c, gin.H{"list": representations})
}

func (a *app) derivativeResource(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, http.StatusNotFound, "派生物资源不存在")
		return
	}
	var assetRow DBAsset
	if err := a.db.Where("id = ? AND owner_id = ? AND status = ?", id, userID(c), "ready").First(&assetRow).Error; err != nil {
		fail(c, http.StatusNotFound, "派生物资源不存在")
		return
	}
	var row DBAssetDerivative
	if err := a.db.Where("asset_id = ? AND kind = ? AND version = ? AND status = ?", id, c.Param("kind"), c.Param("version"), "ready").First(&row).Error; err != nil {
		fail(c, http.StatusNotFound, "派生物资源不存在")
		return
	}
	path, err := derivativeResourcePath(assetFromDB(assetRow), row, c.Param("path"))
	if err != nil {
		fail(c, http.StatusNotFound, "派生物资源不存在")
		return
	}
	serveAssetFile(c.Writer, c.Request, path)
}

const legacyAssetCacheControl = "private, max-age=3600, must-revalidate"

func weakFileETag(info os.FileInfo) string {
	return fmt.Sprintf(`W/"%x-%x"`, info.Size(), info.ModTime().UnixNano())
}

func serveAssetFile(w http.ResponseWriter, request *http.Request, path string) {
	file, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			http.NotFound(w, request)
			return
		}
		http.Error(w, "resource unavailable", http.StatusInternalServerError)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, request)
		return
	}
	w.Header().Set("Cache-Control", legacyAssetCacheControl)
	w.Header().Set("ETag", weakFileETag(info))
	w.Header().Add("Vary", "Authorization")
	http.ServeContent(w, request, filepath.Base(path), info.ModTime(), file)
}

func (a *app) resource(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, http.StatusNotFound, "资源不存在或尚未就绪")
		return
	}
	switch c.Param("resource") {
	case "glb":
		item, found := a.getAssetByID(c, id, "bim")
		if !found {
			fail(c, 404, "资源不存在")
			return
		}
		serveAssetFile(c.Writer, c.Request, filepath.Join(item.Dir, "model.glb"))
	case "metadata":
		item, found := a.getAssetByID(c, id, "bim")
		if !found {
			fail(c, 404, "资源不存在")
			return
		}
		serveAssetFile(c.Writer, c.Request, filepath.Join(item.Dir, "metadata.json"))
	default:
		fail(c, 404, "资源不存在")
	}
}
func (a *app) tile(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, 404, "资源不存在")
		return
	}
	item, found := a.getAssetByID(c, id, "pointcloud")
	if !found {
		fail(c, 404, "资源不存在")
		return
	}
	path, err := safeJoin(filepath.Join(item.Dir, "tiles"), c.Param("path"))
	if err != nil {
		fail(c, 400, err.Error())
		return
	}
	serveAssetFile(c.Writer, c.Request, path)
}

func (a *app) meshAsset(c *gin.Context) (Asset, bool) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil || id <= 0 {
		fail(c, http.StatusBadRequest, "资产 ID 非法")
		return Asset{}, false
	}
	asset, ok := a.getAssetByID(c, id, "bim")
	if !ok {
		fail(c, http.StatusNotFound, "BIM 资产不存在或尚未就绪")
		return Asset{}, false
	}
	return asset, true
}

func (a *app) meshAlgorithms(c *gin.Context) {
	if a.cfg.MeshServiceURL == "" {
		fail(c, http.StatusServiceUnavailable, "未配置网格处理服务，请设置 MESH_SERVICE_URL")
		return
	}
	resp, err := (&http.Client{Timeout: 15 * time.Second}).Get(a.cfg.MeshServiceURL + "/algorithms")
	if err != nil {
		fail(c, http.StatusBadGateway, fmt.Sprintf("调用网格处理服务失败: %v", err))
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fail(c, http.StatusBadGateway, "网格处理服务返回错误")
		return
	}
	var algorithms any
	if err := json.NewDecoder(resp.Body).Decode(&algorithms); err != nil {
		fail(c, http.StatusBadGateway, "解析网格算法列表失败")
		return
	}
	ok(c, algorithms)
}

type meshRemeshStats struct {
	VertexBefore int `json:"vertexBefore"`
	FaceBefore   int `json:"faceBefore"`
	VertexAfter  int `json:"vertexAfter"`
	FaceAfter    int `json:"faceAfter"`
}

const (
	remeshTaskTimeout       = 30 * time.Minute
	remeshRetryAttempts     = 3
	defaultRemeshRetryDelay = 3 * time.Second
	defaultRemeshParamsJSON = `{"target_edge_length":0.1,"clean_tolerance":0.005,"use_decimation":true,"decimation_ratio":0.5,"subdivision_iterations":2,"subdivision_threshold_ratio":2.0,"adaptive":true,"crease_angle":60.0,"use_isotropic":true,"isotropic_iterations":5,"surface_dist_ratio":0.5,"isotropic_collapse":true,"sliver_merge_ratio":0.03,"sliver_relax_checksurfdist":true}`
)

func (a *app) queueRemeshAsset(assetID int64, algorithm, paramsJSON string, force bool) error {
	if strings.TrimSpace(algorithm) == "" {
		algorithm = "bim_preprocessor"
	}
	now := time.Now()
	query := a.db.Model(&DBAsset{}).Where("id = ? AND type = ? AND status = ?", assetID, "bim", "ready")
	if !force {
		query = query.Where("remesh_status IS NULL OR remesh_status = '' OR remesh_status NOT IN ?", []string{"queued", "processing", "succeeded"})
	}
	result := query.Updates(map[string]any{
		"remesh_status":        "queued",
		"remesh_error":         nil,
		"remesh_algorithm":     algorithm,
		"remesh_params_json":   paramsJSON,
		"remesh_queued_at":     &now,
		"remesh_started_at":    nil,
		"remesh_finished_at":   nil,
		"remesh_vertex_before": 0,
		"remesh_face_before":   0,
		"remesh_vertex_after":  0,
		"remesh_face_after":    0,
	})
	if result.Error != nil {
		return fmt.Errorf("保存网格均匀化任务失败: %w", result.Error)
	}
	if result.RowsAffected != 1 {
		return errors.New("BIM 资产不存在、尚未就绪或已有网格任务")
	}
	a.enqueueRemesh(assetID)
	return nil
}

func (a *app) recoverRemeshJobs() error {
	now := time.Now()
	if err := a.db.Model(&DBAsset{}).
		Where("type = ? AND remesh_status = ?", "bim", "processing").
		Updates(map[string]any{
			"remesh_status":      "queued",
			"remesh_error":       "后端服务重启，任务已自动重新排队",
			"remesh_queued_at":   &now,
			"remesh_started_at":  nil,
			"remesh_finished_at": nil,
		}).Error; err != nil {
		return fmt.Errorf("恢复中断的网格均匀化任务失败: %w", err)
	}

	var assets []DBAsset
	if err := a.db.Where("type = ? AND status = ?", "bim", "ready").Order("created_at ASC").Find(&assets).Error; err != nil {
		return fmt.Errorf("读取网格均匀化任务失败: %w", err)
	}
	for _, asset := range assets {
		resultPath := filepath.Join(asset.Dir, "mesh_remesh.ply")
		if asset.RemeshStatus == "" {
			if _, err := os.Stat(resultPath); err == nil {
				_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{
					"remesh_status": "succeeded",
					"remesh_error":  nil,
				}).Error
				continue
			}
			if err := a.queueRemeshAsset(asset.ID, "bim_preprocessor", defaultRemeshParamsJSON, false); err != nil {
				return err
			}
			continue
		}
		if asset.RemeshStatus == "succeeded" {
			if _, err := os.Stat(resultPath); err != nil {
				message := "均匀化结果文件缺失，请重新执行"
				_ = a.finishRemeshJob(asset.ID, nil, "failed", &message, nil)
			}
			continue
		}
		if asset.RemeshStatus == "queued" {
			a.enqueueRemesh(asset.ID)
		}
	}
	return nil
}

func (a *app) processRemeshJob(parent context.Context, assetID int64) {
	now := time.Now()
	claimed := a.db.Model(&DBAsset{}).
		Where("id = ? AND remesh_status = ?", assetID, "queued").
		Updates(map[string]any{
			"remesh_status":      "processing",
			"remesh_error":       nil,
			"remesh_started_at":  &now,
			"remesh_finished_at": nil,
		})
	if claimed.Error != nil || claimed.RowsAffected != 1 {
		return
	}

	var asset DBAsset
	if err := a.db.Where("id = ? AND type = ? AND status = ?", assetID, "bim", "ready").First(&asset).Error; err != nil {
		message := "BIM 资产不存在或尚未就绪"
		_ = a.finishRemeshJob(assetID, nil, "failed", &message, nil)
		return
	}
	startedAt := asset.RemeshStartedAt

	ctx, cancel := context.WithTimeout(parent, remeshTaskTimeout)
	defer cancel()
	stats, err := a.executeRemeshBIM(ctx, asset)
	if err != nil {
		if parent.Err() != nil {
			requeuedAt := time.Now()
			_ = a.db.Model(&DBAsset{}).
				Where("id = ? AND remesh_status = ? AND remesh_started_at = ?", assetID, "processing", startedAt).
				Updates(map[string]any{
					"remesh_status":      "queued",
					"remesh_error":       "后端服务停止，任务将在下次启动时恢复",
					"remesh_queued_at":   &requeuedAt,
					"remesh_started_at":  nil,
					"remesh_finished_at": nil,
				}).Error
			return
		}
		message := err.Error()
		_ = a.finishRemeshJob(assetID, startedAt, "failed", &message, nil)
		log.Printf("BIM 资产 %d 网格均匀化失败: %v", assetID, err)
		return
	}
	if err := a.finishRemeshJob(assetID, startedAt, "succeeded", nil, &stats); err != nil {
		log.Printf("BIM 资产 %d 保存网格均匀化状态失败: %v", assetID, err)
		return
	}
	log.Printf("BIM 资产 %d 网格均匀化完成: vertices %d -> %d, faces %d -> %d", assetID, stats.VertexBefore, stats.VertexAfter, stats.FaceBefore, stats.FaceAfter)
}

func (a *app) finishRemeshJob(assetID int64, startedAt *time.Time, status string, message *string, stats *meshRemeshStats) error {
	updates := map[string]any{
		"remesh_status":      status,
		"remesh_error":       message,
		"remesh_finished_at": time.Now(),
	}
	if stats != nil {
		updates["remesh_vertex_before"] = stats.VertexBefore
		updates["remesh_face_before"] = stats.FaceBefore
		updates["remesh_vertex_after"] = stats.VertexAfter
		updates["remesh_face_after"] = stats.FaceAfter
	}
	query := a.db.Model(&DBAsset{}).Where("id = ?", assetID)
	if startedAt != nil {
		query = query.Where("remesh_status = ? AND remesh_started_at = ?", "processing", startedAt)
	}
	result := query.Updates(updates)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("网格均匀化任务状态已变化")
	}
	return nil
}

func buildRemeshMultipart(inputPath, algorithm, paramsJSON string) (*bytes.Buffer, string, error) {
	input, err := os.Open(inputPath)
	if err != nil {
		return nil, "", fmt.Errorf("打开 BIM GLB 失败: %w", err)
	}
	defer input.Close()

	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	part, err := writer.CreateFormFile("file", filepath.Base(inputPath))
	if err != nil {
		return nil, "", fmt.Errorf("构建网格请求失败: %w", err)
	}
	if _, err := io.Copy(part, input); err != nil {
		return nil, "", fmt.Errorf("读取 BIM GLB 失败: %w", err)
	}
	_ = writer.WriteField("algorithm", algorithm)
	_ = writer.WriteField("params_json", paramsJSON)
	if err := writer.Close(); err != nil {
		return nil, "", fmt.Errorf("构建网格请求失败: %w", err)
	}
	return body, writer.FormDataContentType(), nil
}

func retryAfterDuration(value string, now time.Time, fallback time.Duration) time.Duration {
	value = strings.TrimSpace(value)
	if seconds, err := strconv.Atoi(value); err == nil && seconds >= 0 {
		return time.Duration(seconds) * time.Second
	}
	if retryAt, err := http.ParseTime(value); err == nil {
		if delay := retryAt.Sub(now); delay > 0 {
			return delay
		}
		return 0
	}
	return fallback
}

func copyRetryAfter(destination http.Header, resp *http.Response) {
	if value := strings.TrimSpace(resp.Header.Get("Retry-After")); value != "" {
		destination.Set("Retry-After", value)
	}
}

func waitForRetry(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		return nil
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (a *app) callRemeshService(ctx context.Context, asset DBAsset) (*http.Response, error) {
	if strings.TrimSpace(a.cfg.MeshServiceURL) == "" {
		return nil, errors.New("未配置 MESH_SERVICE_URL")
	}
	inputPath := filepath.Join(asset.Dir, "model.glb")
	algorithm := strings.TrimSpace(asset.RemeshAlgorithm)
	if algorithm == "" {
		algorithm = "bim_preprocessor"
	}
	paramsJSON := strings.TrimSpace(asset.RemeshParamsJSON)
	if paramsJSON == "" {
		paramsJSON = defaultRemeshParamsJSON
	}
	client := &http.Client{Timeout: remeshTaskTimeout}
	var lastErr error
	retryDelay := time.Duration(0)
	for attempt := 0; attempt < remeshRetryAttempts; attempt++ {
		if attempt > 0 {
			if err := waitForRetry(ctx, retryDelay); err != nil {
				return nil, fmt.Errorf("网格均匀化请求已取消: %w", ctx.Err())
			}
		}
		body, contentType, err := buildRemeshMultipart(inputPath, algorithm, paramsJSON)
		if err != nil {
			return nil, err
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, a.cfg.MeshServiceURL+"/remesh", body)
		if err != nil {
			return nil, fmt.Errorf("构建网格请求失败: %w", err)
		}
		request.Header.Set("Content-Type", contentType)
		resp, err := client.Do(request)
		if err != nil {
			lastErr = err
			retryDelay = defaultRemeshRetryDelay
			continue
		}
		if resp.StatusCode != http.StatusOK {
			data, _ := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusTooManyRequests {
				detail := meshServiceError(data)
				lastErr = fmt.Errorf("网格服务正忙: %s", detail)
				if attempt == remeshRetryAttempts-1 {
					return nil, fmt.Errorf("网格服务连续 %d 次返回 429，计算资源仍忙: %s", remeshRetryAttempts, detail)
				}
				retryDelay = retryAfterDuration(resp.Header.Get("Retry-After"), time.Now(), defaultRemeshRetryDelay)
				continue
			}
			return nil, fmt.Errorf("网格服务返回 %d: %s", resp.StatusCode, toolLog(data))
		}
		return resp, nil
	}
	if lastErr == nil {
		lastErr = errors.New("未知网格服务错误")
	}
	return nil, fmt.Errorf("调用网格处理服务失败: %w", lastErr)
}

func (a *app) executeRemeshBIM(ctx context.Context, asset DBAsset) (meshRemeshStats, error) {
	resp, err := a.callRemeshService(ctx, asset)
	if err != nil {
		return meshRemeshStats{}, err
	}
	defer resp.Body.Close()

	tempOutput, err := os.CreateTemp(asset.Dir, ".mesh-remesh-*.ply")
	if err != nil {
		return meshRemeshStats{}, fmt.Errorf("创建均匀化结果临时文件失败: %w", err)
	}
	tempPath := tempOutput.Name()
	defer os.Remove(tempPath)
	if _, err := io.Copy(tempOutput, resp.Body); err != nil {
		_ = tempOutput.Close()
		return meshRemeshStats{}, fmt.Errorf("写入均匀化结果失败: %w", err)
	}
	if err := tempOutput.Close(); err != nil {
		return meshRemeshStats{}, fmt.Errorf("关闭均匀化结果失败: %w", err)
	}
	if err := os.Rename(tempPath, filepath.Join(asset.Dir, "mesh_remesh.ply")); err != nil {
		return meshRemeshStats{}, fmt.Errorf("保存均匀化结果失败: %w", err)
	}
	return meshRemeshStats{
		VertexBefore: headerInt(resp.Header.Get("X-Vertex-Before")),
		FaceBefore:   headerInt(resp.Header.Get("X-Face-Before")),
		VertexAfter:  headerInt(resp.Header.Get("X-Vertex-After")),
		FaceAfter:    headerInt(resp.Header.Get("X-Face-After")),
	}, nil
}

func (a *app) remeshAsset(c *gin.Context) {
	asset, found := a.meshAsset(c)
	if !found {
		return
	}
	if a.cfg.MeshServiceURL == "" {
		fail(c, http.StatusServiceUnavailable, "未配置网格处理服务，请设置 MESH_SERVICE_URL")
		return
	}
	var req struct {
		Algorithm string         `json:"algorithm"`
		Params    map[string]any `json:"params"`
		Force     bool           `json:"force"`
	}
	if c.ShouldBindJSON(&req) != nil {
		fail(c, http.StatusBadRequest, "网格均匀化参数格式非法")
		return
	}
	if req.Algorithm == "" {
		req.Algorithm = "bim_preprocessor"
	}
	if req.Algorithm != "bim_preprocessor" && req.Algorithm != "bim_isotropic_only" {
		fail(c, http.StatusBadRequest, "不支持的网格均匀化算法")
		return
	}
	a.refreshRemeshState(&asset)
	if asset.RemeshStatus == "queued" || asset.RemeshStatus == "processing" {
		fail(c, http.StatusConflict, "网格均匀化任务正在排队或处理中")
		return
	}
	if asset.RemeshStatus == "succeeded" && !req.Force {
		fail(c, http.StatusConflict, "网格均匀化结果已存在")
		return
	}
	paramsJSON, err := json.Marshal(req.Params)
	if err != nil {
		fail(c, http.StatusBadRequest, "网格均匀化参数格式非法")
		return
	}
	if err := a.queueRemeshAsset(asset.ID, req.Algorithm, string(paramsJSON), req.Force); err != nil {
		fail(c, http.StatusConflict, err.Error())
		return
	}
	ok(c, gin.H{"status": "queued", "message": "网格均匀化任务已进入后台队列"})
}

func headerInt(value string) int {
	n, _ := strconv.Atoi(value)
	return n
}

func (a *app) refreshRemeshState(asset *Asset) {
	if asset == nil || asset.Type != "bim" {
		return
	}
	if asset.RemeshStatus == "queued" || asset.RemeshStatus == "processing" {
		var activeSince *time.Time
		if asset.RemeshStatus == "processing" {
			activeSince = asset.RemeshStartedAt
		} else {
			activeSince = asset.RemeshQueuedAt
		}
		if activeSince == nil || time.Since(*activeSince) <= remeshTaskTimeout {
			return
		}
		message := "均匀化任务超过 30 分钟未完成，已自动判定为失败"
		now := time.Now()
		_ = a.db.Model(&DBAsset{}).
			Where("id = ? AND remesh_status IN ?", asset.ID, []string{"queued", "processing"}).
			Updates(map[string]any{
				"remesh_status":      "failed",
				"remesh_error":       message,
				"remesh_finished_at": &now,
			}).Error
		asset.RemeshStatus = "failed"
		asset.RemeshError = &message
		asset.RemeshFinishedAt = &now
		return
	}
	if asset.RemeshStatus == "failed" {
		return
	}

	resultPath := filepath.Join(asset.Dir, "mesh_remesh.ply")
	if asset.RemeshStatus == "" {
		if _, err := os.Stat(resultPath); err == nil {
			now := time.Now()
			_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{
				"remesh_status":      "succeeded",
				"remesh_error":       nil,
				"remesh_finished_at": &now,
			}).Error
			asset.RemeshStatus = "succeeded"
			asset.RemeshError = nil
			asset.RemeshFinishedAt = &now
		}
		return
	}
	if asset.RemeshStatus == "succeeded" {
		if _, err := os.Stat(resultPath); err == nil {
			return
		}
		message := "均匀化结果文件缺失，请重新执行"
		now := time.Now()
		_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{
			"remesh_status":      "failed",
			"remesh_error":       message,
			"remesh_finished_at": &now,
		}).Error
		asset.RemeshStatus = "failed"
		asset.RemeshError = &message
		asset.RemeshFinishedAt = &now
	}
}

func (a *app) remeshStatus(c *gin.Context) {
	asset, found := a.meshAsset(c)
	if !found {
		return
	}
	a.refreshRemeshState(&asset)
	summary := meshRemeshSummary(asset)
	ok(c, summary)
}

func (a *app) remeshLatest(c *gin.Context) {
	asset, found := a.meshAsset(c)
	if !found {
		return
	}
	a.refreshRemeshState(&asset)
	switch asset.RemeshStatus {
	case "queued", "processing":
		fail(c, http.StatusConflict, "网格均匀化任务正在排队或处理中")
		return
	case "failed":
		message := "网格均匀化失败，请重新执行"
		if asset.RemeshError != nil && strings.TrimSpace(*asset.RemeshError) != "" {
			message = *asset.RemeshError
		}
		fail(c, http.StatusConflict, message)
		return
	case "succeeded":
	default:
		fail(c, http.StatusNotFound, "尚无网格均匀化结果")
		return
	}
	path := filepath.Join(asset.Dir, "mesh_remesh.ply")
	if _, err := os.Stat(path); err != nil {
		fail(c, http.StatusNotFound, "尚无网格均匀化结果")
		return
	}
	c.Header("Content-Type", "application/octet-stream")
	c.Header("Content-Disposition", `attachment; filename="remeshed.ply"`)
	c.File(path)
}

func buildBIM(parent context.Context, source, dir string) error {
	// Tus stores the uploaded payload as a normalized `source` path without an
	// extension. The asset type and original extension are validated at upload
	// creation, so checking filepath.Ext(source) here would reject valid files.
	tool, err := resolveTool("IFC_BUNDLE_BIN", "ifc_bundle",
		filepath.Join("..", "tools", "ifc_bundle", "ifc_bundle"),
		filepath.Join("tools", "ifc_bundle", "ifc_bundle"),
		filepath.Join("..", "..", "zhongjian", "zhongjian-back", "tools", "ifc_bundle", "ifc_bundle"),
		filepath.Join("..", "..", "zhongjian-back", "tools", "ifc_bundle", "ifc_bundle"),
	)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(parent, 45*time.Minute)
	defer cancel()

	args := []string{
		"--input", source,
		"--glb-out", filepath.Join(dir, "model.glb"),
		"--meta-out", filepath.Join(dir, "metadata.json"),
	}

	// 自动探测 IfcConvert 路径（优先同目录、环境变量及 fallback）
	toolDir := filepath.Dir(tool)
	ifcConvert, err := resolveTool("IFCCONVERT", "IfcConvert",
		filepath.Join(toolDir, "IfcConvert"),
		filepath.Join("..", "tools", "ifc_bundle", "IfcConvert"),
		filepath.Join("tools", "ifc_bundle", "IfcConvert"),
		filepath.Join("..", "..", "zhongjian", "zhongjian-back", "tools", "ifc_bundle", "IfcConvert"),
		filepath.Join("..", "..", "zhongjian-back", "tools", "ifc_bundle", "IfcConvert"),
	)
	if err == nil && ifcConvert != "" {
		args = append(args, "--ifcconvert", ifcConvert)
	}

	cmd := exec.CommandContext(ctx, tool, args...)
	cmd.Dir = toolDir
	if err == nil && ifcConvert != "" {
		cmd.Env = append(os.Environ(), "IFCCONVERT="+ifcConvert)
	}
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("IFC 转换失败: %s", toolLog(output))
	}
	if _, err := os.Stat(filepath.Join(dir, "model.glb")); err != nil {
		return fmt.Errorf("IFC 转换完成但未生成 GLB")
	}
	if _, err := os.Stat(filepath.Join(dir, "metadata.json")); err != nil {
		return fmt.Errorf("IFC 转换完成但未生成元数据")
	}
	return nil
}
func buildPointCloud(parent context.Context, source, dir string, subsample float64) error {
	// See buildBIM: the normalized Tus source intentionally has no extension.
	tool, err := resolveTool("GOCESIUMTILER_BIN", "gocesiumtiler",
		filepath.Join("..", "tools", "gocesiumtiler", "gocesiumtiler-lin-x64"),
		filepath.Join("tools", "gocesiumtiler", "gocesiumtiler-lin-x64"),
		filepath.Join("..", "..", "zhongjian", "zhongjian-back", "tools", "gocesiumtiler", "gocesiumtiler-lin-x64"),
		filepath.Join("..", "..", "zhongjian-back", "tools", "gocesiumtiler", "gocesiumtiler-lin-x64"),
	)
	if err != nil {
		return err
	}
	// gocesiumtiler expects a LAS filename, while Tus persists all uploads as
	// "source". A link avoids copying a potentially large point-cloud file.
	input := filepath.Join(dir, "source.las")
	if err := linkOrSymlink(source, input); err != nil {
		return fmt.Errorf("准备 LAS 转换输入失败: %w", err)
	}
	tilesDir := filepath.Join(dir, "tiles")
	ctx, cancel := context.WithTimeout(parent, 60*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, tool, "file", "--coord-mode", "source", "--resolution", "1", "--subsample", strconv.FormatFloat(subsample, 'f', -1, 64), "-o", tilesDir, input)
	output, err := cmd.CombinedOutput()
	if err != nil {
		_ = os.RemoveAll(tilesDir)
		return fmt.Errorf("LAS 转 3D Tiles 失败: %s", toolLog(output))
	}
	if _, err := os.Stat(filepath.Join(tilesDir, "tileset.json")); err != nil {
		_ = os.RemoveAll(tilesDir)
		return fmt.Errorf("点云转换完成但未生成 tileset.json")
	}
	return nil
}

func linkOrSymlink(source, target string) error {
	if err := os.Link(source, target); err == nil || os.IsExist(err) {
		return nil
	}
	if err := os.Symlink(source, target); err != nil && !os.IsExist(err) {
		return err
	}
	return nil
}

func resolveTool(envName, command string, fallbacks ...string) (string, error) {
	if value := strings.TrimSpace(os.Getenv(envName)); value != "" {
		if _, err := os.Stat(value); err != nil {
			return "", fmt.Errorf("工具不存在: %s", value)
		}
		if abs, err := filepath.Abs(value); err == nil {
			return abs, nil
		}
		return value, nil
	}
	if value, err := exec.LookPath(command); err == nil {
		if abs, err := filepath.Abs(value); err == nil {
			return abs, nil
		}
		return value, nil
	}
	for _, fallback := range fallbacks {
		if _, err := os.Stat(fallback); err == nil {
			if abs, err := filepath.Abs(fallback); err == nil {
				return abs, nil
			}
			return fallback, nil
		}
	}
	return "", fmt.Errorf("未找到 %s，请设置 %s", command, envName)
}

func toolLog(output []byte) string {
	text := strings.Join(strings.Fields(string(output)), " ")
	if len(text) > 300 {
		return text[:300]
	}
	return text
}

// meshServiceError extracts the useful message from a mesh-service JSON error
// while keeping a bounded fallback for plain-text/proxy responses.
func meshServiceError(output []byte) string {
	var payload struct {
		Msg     string `json:"msg"`
		Message string `json:"message"`
		Detail  string `json:"detail"`
	}
	if err := json.Unmarshal(output, &payload); err == nil {
		for _, value := range []string{payload.Msg, payload.Message, payload.Detail} {
			if value = strings.TrimSpace(value); value != "" {
				return toolLog([]byte(value))
			}
		}
	}
	return toolLog(output)
}

type pair struct {
	SX float64 `json:"modelScanX"`
	SY float64 `json:"modelScanY"`
	SZ float64 `json:"modelScanZ"`
	BX float64 `json:"modelBimX"`
	BY float64 `json:"modelBimY"`
	BZ float64 `json:"modelBimZ"`
}

type rigidFit struct {
	qx, qy, qz, qw, tx, ty, tz float64
	pairCount, inlierCount     int
}
type fitPoint struct{ x, y, z float64 }

func solveRigidFit(pairs []pair) (rigidFit, error) {
	clean := make([]pair, 0, len(pairs))
	for _, p := range pairs {
		values := []float64{p.SX, p.SY, p.SZ, p.BX, p.BY, p.BZ}
		valid := true
		for _, value := range values {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				valid = false
				break
			}
		}
		if valid {
			clean = append(clean, p)
		}
	}
	if len(clean) < 3 {
		return rigidFit{}, errors.New("对齐点数据不完整")
	}
	base, err := fitRigidOnce(clean)
	if err != nil {
		return rigidFit{}, err
	}
	base.pairCount, base.inlierCount = len(clean), len(clean)
	if len(clean) < 6 {
		return base, nil
	}
	type item struct {
		index int
		err   float64
	}
	errs := make([]item, 0, len(clean))
	for i, p := range clean {
		v := applyRigid(base, fitPoint{p.SX, p.SY, p.SZ})
		dx, dy, dz := v.x-p.BX, v.y-p.BY, v.z-p.BZ
		errs = append(errs, item{i, math.Sqrt(dx*dx + dy*dy + dz*dz)})
	}
	sort.Slice(errs, func(i, j int) bool { return errs[i].err < errs[j].err })
	keep := int(math.Ceil(0.8 * float64(len(clean))))
	if keep < 3 {
		keep = 3
	}
	inliers := make([]pair, 0, keep)
	for i := 0; i < keep; i++ {
		inliers = append(inliers, clean[errs[i].index])
	}
	refined, err := fitRigidOnce(inliers)
	if err != nil {
		return base, nil
	}
	refined.pairCount, refined.inlierCount = len(clean), len(inliers)
	return refined, nil
}

func fitRigidOnce(pairs []pair) (rigidFit, error) {
	var sx, sy, sz, bx, by, bz float64
	for _, p := range pairs {
		sx += p.SX
		sy += p.SY
		sz += p.SZ
		bx += p.BX
		by += p.BY
		bz += p.BZ
	}
	n := float64(len(pairs))
	sx, sy, sz, bx, by, bz = sx/n, sy/n, sz/n, bx/n, by/n, bz/n
	var sxx, sxy, sxz, syx, syy, syz, szx, szy, szz, variance float64
	for _, p := range pairs {
		x, y, z := p.SX-sx, p.SY-sy, p.SZ-sz
		u, v, w := p.BX-bx, p.BY-by, p.BZ-bz
		sxx += x * u
		sxy += x * v
		sxz += x * w
		syx += y * u
		syy += y * v
		syz += y * w
		szx += z * u
		szy += z * v
		szz += z * w
		variance += x*x + y*y + z*z
	}
	if variance <= 1e-12 {
		return rigidFit{}, errors.New("对齐点分布退化，无法计算变换")
	}
	N := [4][4]float64{{sxx + syy + szz, syz - szy, szx - sxz, sxy - syx}, {syz - szy, sxx - syy - szz, sxy + syx, szx + sxz}, {szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy}, {sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz}}
	// Correct the symmetric entries explicitly (the compact literal above is easier to audit below).
	N[1][0], N[2][0], N[3][0] = N[0][1], N[0][2], N[0][3]
	N[2][1], N[3][1], N[3][2] = N[1][2], N[1][3], N[2][3]
	values, vectors, ok := jacobiEigen4(N)
	if !ok {
		return rigidFit{}, errors.New("旋转计算失败")
	}
	max := 0
	for i := 1; i < 4; i++ {
		if values[i] > values[max] {
			max = i
		}
	}
	q := normalizeQ([4]float64{vectors[0][max], vectors[1][max], vectors[2][max], vectors[3][max]})
	if q[0] < 0 {
		for i := range q {
			q[i] = -q[i]
		}
	}
	R := rotationFromQ(q)
	return rigidFit{qx: q[1], qy: q[2], qz: q[3], qw: q[0], tx: bx - (R[0][0]*sx + R[0][1]*sy + R[0][2]*sz), ty: by - (R[1][0]*sx + R[1][1]*sy + R[1][2]*sz), tz: bz - (R[2][0]*sx + R[2][1]*sy + R[2][2]*sz)}, nil
}

func jacobiEigen4(input [4][4]float64) ([4]float64, [4][4]float64, bool) {
	a, v := input, [4][4]float64{{1, 0, 0, 0}, {0, 1, 0, 0}, {0, 0, 1, 0}, {0, 0, 0, 1}}
	for iter := 0; iter < 64; iter++ {
		p, q, max := 0, 1, math.Abs(a[0][1])
		for i := 0; i < 4; i++ {
			for j := i + 1; j < 4; j++ {
				if value := math.Abs(a[i][j]); value > max {
					p, q, max = i, j, value
				}
			}
		}
		if max <= 1e-12 {
			return [4]float64{a[0][0], a[1][1], a[2][2], a[3][3]}, v, true
		}
		app, aqq, apq := a[p][p], a[q][q], a[p][q]
		if math.Abs(apq) <= 1e-12 {
			continue
		}
		tau := (aqq - app) / (2 * apq)
		t := 1 / (math.Abs(tau) + math.Sqrt(1+tau*tau))
		if tau < 0 {
			t = -t
		}
		c, s := 1/math.Sqrt(1+t*t), t/math.Sqrt(1+t*t)
		for k := 0; k < 4; k++ {
			if k == p || k == q {
				continue
			}
			akp, akq := a[k][p], a[k][q]
			a[k][p], a[p][k] = c*akp-s*akq, c*akp-s*akq
			a[k][q], a[q][k] = s*akp+c*akq, s*akp+c*akq
		}
		a[p][p], a[q][q], a[p][q], a[q][p] = app-t*apq, aqq+t*apq, 0, 0
		for k := 0; k < 4; k++ {
			vkp, vkq := v[k][p], v[k][q]
			v[k][p], v[k][q] = c*vkp-s*vkq, s*vkp+c*vkq
		}
	}
	return [4]float64{}, [4][4]float64{}, false
}
func normalizeQ(q [4]float64) [4]float64 {
	n := math.Sqrt(q[0]*q[0] + q[1]*q[1] + q[2]*q[2] + q[3]*q[3])
	if n <= 1e-12 {
		return [4]float64{1, 0, 0, 0}
	}
	for i := range q {
		q[i] /= n
	}
	return q
}
func rotationFromQ(q [4]float64) [3][3]float64 {
	w, x, y, z := q[0], q[1], q[2], q[3]
	return [3][3]float64{{1 - 2*(y*y+z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)}, {2 * (x*y + z*w), 1 - 2*(x*x+z*z), 2 * (y*z - x*w)}, {2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2*(x*x+y*y)}}
}
func applyRigid(t rigidFit, p fitPoint) fitPoint {
	R := rotationFromQ(normalizeQ([4]float64{t.qw, t.qx, t.qy, t.qz}))
	return fitPoint{R[0][0]*p.x + R[0][1]*p.y + R[0][2]*p.z + t.tx, R[1][0]*p.x + R[1][1]*p.y + R[1][2]*p.z + t.ty, R[2][0]*p.x + R[2][1]*p.y + R[2][2]*p.z + t.tz}
}

func (a *app) createAlignment(c *gin.Context) {
	var req struct {
		ModelScanFileID int64  `json:"modelScanFileId"`
		ModelBimFileID  int64  `json:"modelBimFileId"`
		ModelPairs      []pair `json:"modelPairs"`
	}
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID == 0 || req.ModelBimFileID == 0 || len(req.ModelPairs) < 3 {
		fail(c, 400, "至少需要 3 组校准点")
		return
	}
	if len(req.ModelPairs) > 64 {
		fail(c, 400, "校准点数量过多，最多支持 64 组")
		return
	}
	if _, ok := a.getAssetByID(c, req.ModelScanFileID, "pointcloud"); !ok {
		fail(c, 404, "点云资产不存在")
		return
	}
	bimAsset, bimOK := a.getAssetByID(c, req.ModelBimFileID, "bim")
	if !bimOK {
		fail(c, 404, "BIM 资产不存在")
		return
	}
	fit, err := solveRigidFit(req.ModelPairs)
	if err != nil {
		fail(c, 400, err.Error())
		return
	}
	qx, qy, qz, qw := fit.qx, fit.qy, fit.qz, fit.qw
	trX, trY, trZ := fit.tx, fit.ty, fit.tz
	R := rotationFromQ([4]float64{qw, qx, qy, qz})
	matrix := []float64{R[0][0], R[1][0], R[2][0], 0, R[0][1], R[1][1], R[2][1], 0, R[0][2], R[1][2], R[2][2], 0, trX, trY, trZ, 1}
	var distances []float64
	for _, p := range req.ModelPairs {
		x, y, z := transform(matrix, p.SX, p.SY, p.SZ)
		distances = append(distances, math.Sqrt((x-p.BX)*(x-p.BX)+(y-p.BY)*(y-p.BY)+(z-p.BZ)*(z-p.BZ)))
	}
	rmse, max := 0.0, 0.0
	for _, e := range distances {
		rmse += e * e
		if e > max {
			max = e
		}
	}
	rmse = math.Sqrt(rmse / float64(len(distances)))
	result := Alignment{ModelID: 0, ScanID: req.ModelScanFileID, BimID: req.ModelBimFileID, ModelBIMBuildingName: stringPtr(bimAsset.SourceName), Qx: qx, Qy: qy, Qz: qz, Qw: qw, Tx: trX, Ty: trY, Tz: trZ, Matrix: matrix, RMSE: rmse, MaxError: max, PairCount: fit.pairCount, InlierCount: fit.inlierCount}
	matrixJSON, _ := json.Marshal(result.Matrix)
	row := DBAlignment{ScanID: result.ScanID, BimID: result.BimID, Qx: result.Qx, Qy: result.Qy, Qz: result.Qz, Qw: result.Qw, Tx: result.Tx, Ty: result.Ty, Tz: result.Tz, MatrixJSON: string(matrixJSON), RMSE: result.RMSE, MaxError: result.MaxError, PairCount: result.PairCount, InlierCount: result.InlierCount, OwnerID: userID(c), CreatedAt: time.Now()}
	var existing DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", row.ScanID, row.BimID, row.OwnerID).First(&existing).Error; err == nil {
		row.ID = existing.ID
		if err := a.db.Save(&row).Error; err != nil {
			fail(c, 500, "保存对齐结果失败")
			return
		}
	} else if errors.Is(err, gorm.ErrRecordNotFound) {
		if err := a.db.Create(&row).Error; err != nil {
			fail(c, 500, "保存对齐结果失败")
			return
		}
	} else {
		fail(c, 500, "查询对齐结果失败")
		return
	}
	result.ModelID = row.ID
	ok(c, result)
}
func quatFromBasis(ux, uy, uz, vx, vy, vz, wx, wy, wz float64) (float64, float64, float64, float64) {
	m00, m01, m02 := ux, vx, wx
	m10, m11, m12 := uy, vy, wy
	m20, m21, m22 := uz, vz, wz
	tr := m00 + m11 + m22
	var x, y, z, w float64
	if tr > 0 {
		s := math.Sqrt(tr+1) * 2
		w, x, y, z = .25*s, (m21-m12)/s, (m02-m20)/s, (m10-m01)/s
	} else {
		w = 1
	}
	return x, y, z, w
}
func transform(m []float64, x, y, z float64) (float64, float64, float64) {
	return m[0]*x + m[4]*y + m[8]*z + m[12], m[1]*x + m[5]*y + m[9]*z + m[13], m[2]*x + m[6]*y + m[10]*z + m[14]
}
func (a *app) getAssetByID(c *gin.Context, id int64, typ string) (Asset, bool) {
	var row DBAsset
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", id, userID(c), typ, "ready").First(&row).Error; err != nil {
		return Asset{}, false
	}
	return assetFromDB(row), true
}
func (a *app) getAlignment(c *gin.Context) {
	scan, _ := strconv.ParseInt(c.Query("modelScanFileId"), 10, 64)
	bim, _ := strconv.ParseInt(c.Query("modelBimFileId"), 10, 64)
	var row DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan, bim, userID(c)).First(&row).Error; err != nil {
		fail(c, 404, "校准结果不存在")
		return
	}
	var matrix []float64
	_ = json.Unmarshal([]byte(row.MatrixJSON), &matrix)
	bimAsset, _ := a.getAssetByID(c, row.BimID, "bim")
	ok(c, Alignment{ModelID: row.ID, ScanID: row.ScanID, BimID: row.BimID, ModelBIMBuildingName: stringPtr(bimAsset.SourceName), Qx: row.Qx, Qy: row.Qy, Qz: row.Qz, Qw: row.Qw, Tx: row.Tx, Ty: row.Ty, Tz: row.Tz, Matrix: matrix, RMSE: row.RMSE, MaxError: row.MaxError, PairCount: row.PairCount, InlierCount: row.InlierCount})
}

func stringPtr(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}

type fineAlignmentMetrics struct {
	InitFitness       float64 `json:"initFitness"`
	InitRMSE          float64 `json:"initRmse"`
	FineFitness       float64 `json:"fineFitness"`
	FineRMSE          float64 `json:"fineRmse"`
	DeltaTranslationM float64 `json:"deltaTranslationM"`
	DeltaRotationDeg  float64 `json:"deltaRotationDeg"`
	ElapsedS          float64 `json:"elapsedS"`
	SourceTotalPoints int     `json:"sourceTotalPoints"`
	TargetPoints      int     `json:"targetPoints"`
}

type fineAlignmentResponse struct {
	ModelScanFileID      int64                `json:"modelScanFileId"`
	ModelBimFileID       int64                `json:"modelBimFileId"`
	ModelBIMBuildingName *string              `json:"modelBimBuildingName,omitempty"`
	ModelMatrix          []float64            `json:"modelMatrix"`
	Fallback             bool                 `json:"fallback"`
	Regressed            bool                 `json:"regressed"`
	AppliedFineResult    bool                 `json:"appliedFineResult"`
	Metrics              fineAlignmentMetrics `json:"metrics"`
	RMSERegressRatio     float64              `json:"rmseRegressRatio"`
	FitnessRegressRatio  float64              `json:"fitnessRegressRatio"`
	ApplyWhenRegressed   bool                 `json:"applyWhenRegressed"`
	ModelRotationQx      float64              `json:"modelRotationQx"`
	ModelRotationQy      float64              `json:"modelRotationQy"`
	ModelRotationQz      float64              `json:"modelRotationQz"`
	ModelRotationQw      float64              `json:"modelRotationQw"`
	ModelTranslationX    float64              `json:"modelTranslationX"`
	ModelTranslationY    float64              `json:"modelTranslationY"`
	ModelTranslationZ    float64              `json:"modelTranslationZ"`
}

// fineAlignmentServiceResponse mirrors the mesh-service payload. Keeping this
// separate from the public API response prevents the Python field names from
// leaking into the browser contract.
type fineAlignmentServiceResponse struct {
	Transform  []float64 `json:"transform"`
	Quaternion struct {
		Qx float64 `json:"qx"`
		Qy float64 `json:"qy"`
		Qz float64 `json:"qz"`
		Qw float64 `json:"qw"`
	} `json:"quaternion"`
	Translation struct {
		Tx float64 `json:"tx"`
		Ty float64 `json:"ty"`
		Tz float64 `json:"tz"`
	} `json:"translation"`
	Fallback  bool                 `json:"fallback"`
	Regressed bool                 `json:"regressed"`
	Applied   bool                 `json:"appliedFineResult"`
	Metrics   fineAlignmentMetrics `json:"metrics"`
}

// fineAlignment proxies an optional mesh service, matching the reference project's ICP endpoint.
func (a *app) fineAlignment(c *gin.Context) {
	var req struct {
		ModelScanFileID           int64   `json:"modelScanFileId"`
		ModelBimFileID            int64   `json:"modelBimFileId"`
		MaxCorrespondenceDistance float64 `json:"maxCorrespondenceDistance"`
		RMSERegressRatio          float64 `json:"rmseRegressRatio"`
		FitnessRegressRatio       float64 `json:"fitnessRegressRatio"`
		ApplyWhenRegressed        bool    `json:"applyWhenRegressed"`
	}
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID <= 0 || req.ModelBimFileID <= 0 {
		fail(c, 400, "文件 ID 非法")
		return
	}
	if a.cfg.MeshServiceURL == "" {
		fail(c, 503, "未配置精细化配准服务，请设置 MESH_SERVICE_URL")
		return
	}
	scan, scanOK := a.getAssetByID(c, req.ModelScanFileID, "pointcloud")
	bim, bimOK := a.getAssetByID(c, req.ModelBimFileID, "bim")
	if !scanOK || !bimOK {
		fail(c, 404, "点云或 BIM 资产不存在或尚未就绪")
		return
	}
	a.refreshRemeshState(&bim)
	switch bim.RemeshStatus {
	case "queued", "processing":
		fail(c, http.StatusConflict, "BIM 网格均匀化正在处理中，请稍后重试")
		return
	case "failed":
		message := "BIM 网格均匀化失败，请重新执行"
		if bim.RemeshError != nil && strings.TrimSpace(*bim.RemeshError) != "" {
			message = "BIM 网格均匀化失败: " + *bim.RemeshError
		}
		fail(c, http.StatusConflict, message)
		return
	case "succeeded":
	default:
		fail(c, http.StatusServiceUnavailable, "BIM 尚未生成精调所需的均匀化网格")
		return
	}
	sourcePath, err := a.resolveScanSourcePath(scan, userID(c))
	if err != nil {
		fail(c, 404, "点云源文件不存在")
		return
	}
	meshPath := filepath.Join(bim.Dir, "mesh_remesh.ply")
	if _, err := os.Stat(meshPath); err != nil {
		fail(c, 503, "BIM 尚未生成精调所需的均匀化网格")
		return
	}
	var alignment DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, userID(c)).First(&alignment).Error; err != nil {
		fail(c, 404, "请先保存粗配准矩阵")
		return
	}
	var initialMatrix []float64
	_ = json.Unmarshal([]byte(alignment.MatrixJSON), &initialMatrix)
	if len(initialMatrix) != 16 {
		fail(c, 500, "粗配准矩阵格式非法")
		return
	}
	if req.MaxCorrespondenceDistance <= 0 {
		req.MaxCorrespondenceDistance = 0.3
	}
	if req.RMSERegressRatio <= 0 {
		req.RMSERegressRatio = 1.05
	}
	if req.FitnessRegressRatio <= 0 {
		req.FitnessRegressRatio = 0.95
	}
	serviceScanPath, serviceMeshPath := meshServiceInputPaths(a.cfg.DataDir, a.cfg.MeshServiceStorageDir, sourcePath, meshPath)
	body, err := json.Marshal(map[string]any{
		"scan_path":                   serviceScanPath,
		"mesh_path":                   serviceMeshPath,
		"init_transform":              initialMatrix,
		"max_correspondence_distance": req.MaxCorrespondenceDistance,
		"rmse_regress_ratio":          req.RMSERegressRatio,
		"fitness_regress_ratio":       req.FitnessRegressRatio,
		"apply_when_regressed":        req.ApplyWhenRegressed,
	})
	if err != nil {
		fail(c, 500, "构建精调请求失败")
		return
	}
	request, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, a.cfg.MeshServiceURL+"/align/fine", bytes.NewReader(body))
	if err != nil {
		fail(c, 500, "构建精调请求失败")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 30 * time.Minute}).Do(request)
	if err != nil {
		fail(c, 502, fmt.Sprintf("调用精细化配准服务失败: %v", err))
		return
	}
	defer resp.Body.Close()
	responseBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		if resp.StatusCode == http.StatusTooManyRequests {
			copyRetryAfter(c.Writer.Header(), resp)
			detail := meshServiceError(responseBody)
			message := "精细化配准服务正忙，请稍后重试"
			if detail != "" {
				message += ": " + detail
			}
			fail(c, http.StatusTooManyRequests, message)
			return
		}
		fail(c, 502, fmt.Sprintf("精细化配准服务返回错误(%d): %s", resp.StatusCode, toolLog(responseBody)))
		return
	}
	var serviceResult fineAlignmentServiceResponse
	if err := json.Unmarshal(responseBody, &serviceResult); err != nil {
		fail(c, 502, "解析精细化配准结果失败")
		return
	}
	result := fineAlignmentResponse{
		ModelMatrix:       serviceResult.Transform,
		Fallback:          serviceResult.Fallback,
		Regressed:         serviceResult.Regressed,
		AppliedFineResult: serviceResult.Applied,
		Metrics:           serviceResult.Metrics,
		ModelRotationQx:   serviceResult.Quaternion.Qx,
		ModelRotationQy:   serviceResult.Quaternion.Qy,
		ModelRotationQz:   serviceResult.Quaternion.Qz,
		ModelRotationQw:   serviceResult.Quaternion.Qw,
		ModelTranslationX: serviceResult.Translation.Tx,
		ModelTranslationY: serviceResult.Translation.Ty,
		ModelTranslationZ: serviceResult.Translation.Tz,
	}
	result.ModelScanFileID, result.ModelBimFileID = scan.ID, bim.ID
	result.ModelBIMBuildingName = stringPtr(bim.SourceName)
	result.RMSERegressRatio, result.FitnessRegressRatio, result.ApplyWhenRegressed = req.RMSERegressRatio, req.FitnessRegressRatio, req.ApplyWhenRegressed
	if serviceResult.Applied && len(serviceResult.Transform) == 16 {
		matrixJSON, _ := json.Marshal(serviceResult.Transform)
		_ = a.db.Model(&DBAlignment{}).
			Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, userID(c)).
			Updates(map[string]any{
				"qx":          serviceResult.Quaternion.Qx,
				"qy":          serviceResult.Quaternion.Qy,
				"qz":          serviceResult.Quaternion.Qz,
				"qw":          serviceResult.Quaternion.Qw,
				"tx":          serviceResult.Translation.Tx,
				"ty":          serviceResult.Translation.Ty,
				"tz":          serviceResult.Translation.Tz,
				"matrix_json": string(matrixJSON),
				"rmse":        serviceResult.Metrics.FineRMSE,
			}).Error
	}
	ok(c, result)
}

type c2mStats struct {
	Min                  float64 `json:"min"`
	Max                  float64 `json:"max"`
	Mean                 float64 `json:"mean"`
	Std                  float64 `json:"std"`
	P50                  float64 `json:"p50"`
	P90                  float64 `json:"p90"`
	P95                  float64 `json:"p95"`
	P99                  float64 `json:"p99"`
	MeanAbs              float64 `json:"meanAbs"`
	RMSE                 float64 `json:"rmse"`
	P95Abs               float64 `json:"p95Abs"`
	WithinToleranceRatio float64 `json:"withinToleranceRatio"`
}

type c2mRequest struct {
	ModelScanFileID         int64   `json:"modelScanFileId"`
	ModelBimFileID          int64   `json:"modelBimFileId"`
	Profile                 string  `json:"profile"`
	VoxelSize               float64 `json:"voxelSize"`
	MaxColormapDistance     float64 `json:"maxColormapDistance"`
	MaxHistogramDistance    float64 `json:"maxHistogramDistance"`
	HistogramBins           int     `json:"histogramBins"`
	ToleranceLimit          float64 `json:"toleranceLimit"`
	KnnK                    int     `json:"knnK"`
	NormalConstraintEnabled bool    `json:"normalConstraintEnabled"`
	NormalHalfSpaceOnly     *bool   `json:"normalHalfSpaceOnly"`
	NormalMaxAngleDeg       float64 `json:"normalMaxAngleDeg"`
	NormalFallbackMode      string  `json:"normalFallbackMode"`
}

type c2mServiceResult struct {
	Profile          string            `json:"profile"`
	AlgorithmVersion string            `json:"algorithmVersion"`
	MetricDirection  string            `json:"metricDirection"`
	Approximation    json.RawMessage   `json:"approximation"`
	PointsBefore     int               `json:"pointsBefore"`
	PointsAfter      int               `json:"pointsAfter"`
	MeshVertices     int               `json:"meshVertices"`
	Stats            c2mStats          `json:"stats"`
	Histogram        json.RawMessage   `json:"histogram"`
	Visualization    *c2mVisualization `json:"visualization"`
	Diagnostics      json.RawMessage   `json:"diagnostics"`
	ColoredPlyPath   string            `json:"coloredPlyPath"`
	DistancesPath    string            `json:"distancesPath"`
}

type c2mRecolorRequest struct {
	ModelScanFileID      int64   `json:"modelScanFileId"`
	ModelBimFileID       int64   `json:"modelBimFileId"`
	MaxColormapDistance  float64 `json:"maxColormapDistance"`
	MaxHistogramDistance float64 `json:"maxHistogramDistance"`
	HistogramBins        int     `json:"histogramBins"`
	ToleranceLimit       float64 `json:"toleranceLimit"`
}

type c2mRecolorServiceResult struct {
	Stats          c2mStats          `json:"stats"`
	Histogram      json.RawMessage   `json:"histogram"`
	Visualization  *c2mVisualization `json:"visualization"`
	ColoredPlyPath string            `json:"coloredPlyPath"`
	ColoredPlySize int64             `json:"coloredPlySize"`
}

type c2mVisualization struct {
	MaxColormapDistance  float64 `json:"maxColormapDistance"`
	MaxHistogramDistance float64 `json:"maxHistogramDistance"`
	HistogramBins        int     `json:"histogramBins"`
	ToleranceLimit       float64 `json:"toleranceLimit"`
	ColorDistanceField   string  `json:"colorDistanceField,omitempty"`
	SmoothingIterations  int     `json:"smoothingIterations,omitempty"`
	SmoothingStrength    float64 `json:"smoothingStrength,omitempty"`
}

func (a *app) resolveScanSourcePath(scan Asset, ownerID int64) (string, error) {
	// 1. 优先检查资产目录内部是否已有归档的源点云文件 (source.las 或 source)
	for _, name := range []string{"source.las", "source"} {
		p := filepath.Join(scan.Dir, name)
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}

	// 2. 查询该资产关联的上传记录，优先选择已就绪（status = 'ready'）且最新的记录
	var uploads []DBUpload
	query := a.db.Where("asset_id = ?", scan.ID)
	if ownerID > 0 {
		query = query.Where("owner_id = ?", ownerID)
	}
	if err := query.Order("CASE WHEN status = 'ready' THEN 0 ELSE 1 END, created_at DESC").Find(&uploads).Error; err != nil || len(uploads) == 0 {
		return "", errors.New("点云源文件记录不存在")
	}

	// 3. 逐个候选检查物理文件是否存在（兼容不同挂载或历史路径）
	for _, u := range uploads {
		candidate := filepath.Join(u.Dir, "source")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
		candidate = filepath.Join(a.cfg.DataDir, "uploads", u.ID, "source")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}

	return "", errors.New("点云源文件物理文件不存在")
}

func meshServicePath(dataDir, path, serviceStorageDir string) string {
	cleanRoot := filepath.Clean(dataDir)
	cleanPath := filepath.Clean(path)
	if rel, err := filepath.Rel(cleanRoot, cleanPath); err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		root := strings.TrimSpace(serviceStorageDir)
		if root == "" {
			root = "/storage"
		}
		return filepath.ToSlash(filepath.Join(root, rel))
	}
	return path
}

func meshServiceInputPaths(dataDir, serviceStorageDir, scanPath, meshPath string) (string, string) {
	return meshServicePath(dataDir, scanPath, serviceStorageDir), meshServicePath(dataDir, meshPath, serviceStorageDir)
}

func backendDataPath(dataDir, path, serviceStorageDir string) string {
	serviceRoot := strings.TrimRight(filepath.ToSlash(strings.TrimSpace(serviceStorageDir)), "/")
	if serviceRoot == "" {
		serviceRoot = "/storage"
	}
	pathSlash := filepath.ToSlash(path)
	if strings.HasPrefix(pathSlash, serviceRoot+"/") {
		return filepath.Join(dataDir, filepath.FromSlash(strings.TrimPrefix(pathSlash, serviceRoot+"/")))
	}
	return path
}

func c2mInputFingerprint(matrixJSON, scanPath, meshPath string) (string, error) {
	hash := sha256.New()
	_, _ = io.WriteString(hash, strings.TrimSpace(matrixJSON))
	for _, path := range []string{scanPath, meshPath} {
		info, err := os.Stat(path)
		if err != nil {
			return "", err
		}
		_, _ = fmt.Fprintf(hash, "\x00%s\x00%d\x00%d", filepath.Clean(path), info.Size(), info.ModTime().UnixNano())
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func (a *app) currentC2MInputFingerprint(scanID, bimID, ownerID int64) (string, error) {
	var scanRow, bimRow DBAsset
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", scanID, ownerID, "pointcloud", "ready").First(&scanRow).Error; err != nil {
		return "", errors.New("点云资产不可用")
	}
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", bimID, ownerID, "bim", "ready").First(&bimRow).Error; err != nil {
		return "", errors.New("BIM 资产不可用")
	}
	var alignment DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, ownerID).First(&alignment).Error; err != nil {
		return "", errors.New("配准结果不存在")
	}
	scanPath, err := a.resolveScanSourcePath(assetFromDB(scanRow), ownerID)
	if err != nil {
		return "", err
	}
	meshPath := filepath.Join(bimRow.Dir, "mesh_remesh.ply")
	return c2mInputFingerprint(alignment.MatrixJSON, scanPath, meshPath)
}

func (a *app) c2mFreshness(row DBC2MResult) (bool, string) {
	if strings.TrimSpace(row.InputFingerprint) == "" {
		return false, "历史结果缺少输入版本，请重新计算"
	}
	current, err := a.currentC2MInputFingerprint(row.ScanID, row.BimID, row.OwnerID)
	if err != nil {
		return false, err.Error()
	}
	if current != row.InputFingerprint {
		return false, "配准矩阵、点云源文件或网格均匀化结果已变化，请重新计算"
	}
	return true, ""
}

func (a *app) c2mArtifactPath(path string) (string, error) {
	if strings.TrimSpace(path) == "" {
		return "", errors.New("C2M 产物路径为空")
	}
	root, err := filepath.Abs(filepath.Join(a.cfg.DataDir, "c2m_results"))
	if err != nil {
		return "", err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	resolved, err = filepath.EvalSymlinks(resolved)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, resolved)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", errors.New("C2M 产物路径超出允许目录")
	}
	return resolved, nil
}

func (a *app) removeC2MArtifact(path string) {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return
	}
	resolved, err := a.c2mArtifactPath(path)
	if err == nil {
		_ = os.Remove(resolved)
	}
}

func (a *app) removeUnreferencedC2MArtifact(path string) {
	resolved, err := a.c2mArtifactPath(path)
	if err != nil {
		return
	}
	var references int64
	if err := a.db.Model(&DBC2MResult{}).
		Where("colored_ply_path = ? OR distances_path = ?", resolved, resolved).
		Count(&references).Error; err != nil || references != 0 {
		return
	}
	a.removeC2MArtifact(resolved)
}

func (a *app) c2mArtifactAvailable(path string) bool {
	resolved, err := a.c2mArtifactPath(path)
	return err == nil && regularFileExists(resolved)
}

func (a *app) c2mResultResponse(row DBC2MResult) gin.H {
	data := c2mResultData(row)
	fresh, reason := a.c2mFreshness(row)
	data["fresh"] = fresh
	data["coloredPlyAvailable"] = a.c2mArtifactAvailable(row.ColoredPlyPath)
	data["distancesAvailable"] = a.c2mArtifactAvailable(row.DistancesPath)
	if reason != "" {
		data["staleReason"] = reason
	}
	return data
}

func normalizeC2MProfile(profile string) string {
	profile = strings.ToLower(strings.TrimSpace(profile))
	if profile == "" {
		return "quick"
	}
	return profile
}

func resolveC2MResultProfile(requested, returned string) (string, error) {
	requested = normalizeC2MProfile(requested)
	if strings.TrimSpace(returned) == "" {
		if requested != "quick" {
			return "", fmt.Errorf("C2M 服务未声明 %s 计算结果的 profile", requested)
		}
		return "quick", nil
	}
	returned = normalizeC2MProfile(returned)
	if returned != requested {
		return "", fmt.Errorf("C2M 服务返回 profile %s，与请求的 %s 不一致", returned, requested)
	}
	return returned, nil
}

func c2mServiceParams(req c2mRequest) map[string]any {
	params := map[string]any{
		"profile":                   normalizeC2MProfile(req.Profile),
		"voxel_size":                req.VoxelSize,
		"max_colormap_distance":     req.MaxColormapDistance,
		"max_histogram_distance":    req.MaxHistogramDistance,
		"histogram_bins":            req.HistogramBins,
		"tolerance_limit":           req.ToleranceLimit,
		"smoothing_iterations":      0,
		"smoothing_strength":        0.5,
		"knn_k":                     req.KnnK,
		"normal_constraint_enabled": req.NormalConstraintEnabled,
		"normal_max_angle_deg":      req.NormalMaxAngleDeg,
		"normal_fallback_mode":      req.NormalFallbackMode,
	}
	if req.NormalHalfSpaceOnly != nil {
		params["normal_half_space_only"] = *req.NormalHalfSpaceOnly
	}
	return params
}

func validateC2MVisualizationRanges(maxColormapDistance, maxHistogramDistance float64, histogramBins int, toleranceLimit float64) error {
	switch {
	case math.IsNaN(maxColormapDistance) || math.IsInf(maxColormapDistance, 0) || maxColormapDistance < 0.001 || maxColormapDistance > 10:
		return errors.New("maxColormapDistance 必须在 0.001 到 10 m 之间")
	case math.IsNaN(maxHistogramDistance) || math.IsInf(maxHistogramDistance, 0) || maxHistogramDistance < 0.001 || maxHistogramDistance > 10:
		return errors.New("maxHistogramDistance 必须在 0.001 到 10 m 之间")
	case histogramBins < 10 || histogramBins > 200:
		return errors.New("histogramBins 必须在 10 到 200 之间")
	case math.IsNaN(toleranceLimit) || math.IsInf(toleranceLimit, 0) || toleranceLimit < 0.0001 || toleranceLimit > maxColormapDistance:
		return errors.New("toleranceLimit 必须大于 0 且不能超过配色色域")
	}
	return nil
}

func normalizeC2MRequest(req *c2mRequest) error {
	if req.VoxelSize == 0 {
		req.VoxelSize = 0.05
	}
	if req.MaxColormapDistance == 0 {
		req.MaxColormapDistance = 0.10
	}
	if req.MaxHistogramDistance == 0 {
		req.MaxHistogramDistance = 0.10
	}
	if req.HistogramBins == 0 {
		req.HistogramBins = 50
	}
	if req.ToleranceLimit == 0 {
		req.ToleranceLimit = math.Min(0.05, req.MaxColormapDistance)
	}
	if req.KnnK == 0 {
		req.KnnK = 8
	}
	if req.NormalMaxAngleDeg == 0 {
		req.NormalMaxAngleDeg = 75
	}
	if req.NormalFallbackMode == "" {
		req.NormalFallbackMode = "nearest"
	}
	req.Profile = normalizeC2MProfile(req.Profile)

	switch {
	case req.Profile != "quick" && req.Profile != "reference":
		return fmt.Errorf("不支持的 C2M profile: %s", req.Profile)
	case math.IsNaN(req.VoxelSize) || math.IsInf(req.VoxelSize, 0) || req.VoxelSize < 0.001 || req.VoxelSize > 5:
		return errors.New("voxelSize 必须在 0.001 到 5 m 之间")
	case req.KnnK < 1 || req.KnnK > 64:
		return errors.New("knnK 必须在 1 到 64 之间")
	case math.IsNaN(req.NormalMaxAngleDeg) || math.IsInf(req.NormalMaxAngleDeg, 0) || req.NormalMaxAngleDeg <= 0 || req.NormalMaxAngleDeg > 180:
		return errors.New("normalMaxAngleDeg 必须在 0 到 180 度之间")
	case req.NormalFallbackMode != "nearest":
		return errors.New("normalFallbackMode 当前仅支持 nearest")
	}
	return validateC2MVisualizationRanges(req.MaxColormapDistance, req.MaxHistogramDistance, req.HistogramBins, req.ToleranceLimit)
}

func normalizeC2MRecolorRequest(req *c2mRecolorRequest) error {
	compute := c2mRequest{
		VoxelSize:            0.05,
		MaxColormapDistance:  req.MaxColormapDistance,
		MaxHistogramDistance: req.MaxHistogramDistance,
		HistogramBins:        req.HistogramBins,
		ToleranceLimit:       req.ToleranceLimit,
		KnnK:                 8,
		NormalMaxAngleDeg:    75,
		NormalFallbackMode:   "nearest",
	}
	if err := normalizeC2MRequest(&compute); err != nil {
		return err
	}
	req.MaxColormapDistance = compute.MaxColormapDistance
	req.MaxHistogramDistance = compute.MaxHistogramDistance
	req.HistogramBins = compute.HistogramBins
	req.ToleranceLimit = compute.ToleranceLimit
	return nil
}

func c2mVisualizationFromRequest(req c2mRequest) c2mVisualization {
	return c2mVisualization{
		MaxColormapDistance:  req.MaxColormapDistance,
		MaxHistogramDistance: req.MaxHistogramDistance,
		HistogramBins:        req.HistogramBins,
		ToleranceLimit:       req.ToleranceLimit,
		ColorDistanceField:   "raw",
		SmoothingIterations:  0,
		SmoothingStrength:    0.5,
	}
}

func resolveC2MVisualization(returned *c2mVisualization, fallback c2mVisualization) (c2mVisualization, error) {
	if returned == nil {
		return fallback, nil
	}
	result := *returned
	if err := validateC2MVisualizationRanges(result.MaxColormapDistance, result.MaxHistogramDistance, result.HistogramBins, result.ToleranceLimit); err != nil {
		return c2mVisualization{}, fmt.Errorf("C2M 服务返回的可视化参数非法: %w", err)
	}
	if result.SmoothingIterations < 0 || result.SmoothingIterations > 100 || math.IsNaN(result.SmoothingStrength) || math.IsInf(result.SmoothingStrength, 0) || result.SmoothingStrength < 0 || result.SmoothingStrength > 1 {
		return c2mVisualization{}, errors.New("C2M 服务返回的平滑参数非法")
	}
	if result.ColorDistanceField == "" {
		if result.SmoothingIterations == 0 {
			result.ColorDistanceField = "raw"
		} else {
			result.ColorDistanceField = "smoothed"
		}
	}
	if result.ColorDistanceField != "raw" && result.ColorDistanceField != "smoothed" {
		return c2mVisualization{}, errors.New("C2M 服务返回的 colorDistanceField 非法")
	}
	return result, nil
}

func validC2MHistogram(value json.RawMessage) bool {
	var histogram struct {
		BinEdges      []float64 `json:"binEdges"`
		Counts        []int64   `json:"counts"`
		OverflowCount int64     `json:"overflowCount"`
	}
	if len(value) == 0 || json.Unmarshal(value, &histogram) != nil || len(histogram.Counts) == 0 || len(histogram.BinEdges) != len(histogram.Counts)+1 || histogram.OverflowCount < 0 {
		return false
	}
	for index, edge := range histogram.BinEdges {
		if math.IsNaN(edge) || math.IsInf(edge, 0) || (index > 0 && edge <= histogram.BinEdges[index-1]) {
			return false
		}
	}
	for _, count := range histogram.Counts {
		if count < 0 {
			return false
		}
	}
	return true
}

func isHistoricalC2MResult(row DBC2MResult) bool {
	return strings.TrimSpace(row.Profile) == "" && strings.TrimSpace(row.AlgorithmVersion) == "" && strings.TrimSpace(row.MetricDirection) == ""
}

func c2mStatsFromRow(row DBC2MResult) gin.H {
	stats := gin.H{
		"min":  row.MinDist,
		"max":  row.MaxDist,
		"mean": row.MeanDist,
		"std":  row.StdDist,
		"p50":  row.P50,
		"p90":  row.P90,
		"p95":  row.P95,
		"p99":  row.P99,
	}
	if !isHistoricalC2MResult(row) {
		stats["meanAbs"] = row.MeanAbs
		stats["rmse"] = row.RMSE
		stats["p95Abs"] = row.P95Abs
		stats["withinToleranceRatio"] = row.WithinToleranceRatio
	}
	return stats
}

func validRawJSON(value string, fallback json.RawMessage) json.RawMessage {
	value = strings.TrimSpace(value)
	if value != "" && json.Valid([]byte(value)) {
		return json.RawMessage(value)
	}
	return fallback
}

func regularFileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func c2mResultData(row DBC2MResult) gin.H {
	approximationFallback, _ := json.Marshal(map[string]any{"voxelSize": row.VoxelSize})
	maxColormapDistance := row.MaxColormapDistance
	if maxColormapDistance <= 0 {
		maxColormapDistance = 0.10
	}
	maxHistogramDistance := row.MaxHistogramDistance
	if maxHistogramDistance <= 0 {
		maxHistogramDistance = 0.10
	}
	histogramBins := row.HistogramBins
	if histogramBins <= 0 {
		histogramBins = 50
	}
	toleranceLimit := row.ToleranceLimit
	if toleranceLimit <= 0 {
		toleranceLimit = 0.05
	}
	return gin.H{
		"modelScanFileId":  row.ScanID,
		"modelBimFileId":   row.BimID,
		"profile":          normalizeC2MProfile(row.Profile),
		"algorithmVersion": row.AlgorithmVersion,
		"metricDirection":  row.MetricDirection,
		"approximation":    validRawJSON(row.ApproximationJSON, approximationFallback),
		"voxelSize":        row.VoxelSize,
		"pointsBefore":     row.PointsBefore,
		"pointsAfter":      row.PointsAfter,
		"meshVertexCount":  row.MeshVertexCount,
		"stats":            c2mStatsFromRow(row),
		"histogram":        validRawJSON(row.HistogramJSON, json.RawMessage("null")),
		"visualization": gin.H{
			"maxColormapDistance":  maxColormapDistance,
			"maxHistogramDistance": maxHistogramDistance,
			"histogramBins":        histogramBins,
			"toleranceLimit":       toleranceLimit,
			"colorDistanceField":   "raw",
			"smoothingIterations":  0,
			"smoothingStrength":    0.5,
		},
		"diagnostics":      validRawJSON(row.DiagnosticsJSON, json.RawMessage("{}")),
		"inputFingerprint": row.InputFingerprint,
		"createdAt":        row.CreatedAt,
		"updatedAt":        row.UpdatedAt,
	}
}

func (a *app) computeC2M(c *gin.Context) {
	var req c2mRequest
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID <= 0 || req.ModelBimFileID <= 0 {
		fail(c, http.StatusBadRequest, "Scan 与 BIM 资产 ID 非法")
		return
	}
	scan, scanOK := a.getAssetByID(c, req.ModelScanFileID, "pointcloud")
	bim, bimOK := a.getAssetByID(c, req.ModelBimFileID, "bim")
	if !scanOK || !bimOK {
		fail(c, http.StatusNotFound, "点云或 BIM 资产不存在或尚未就绪")
		return
	}
	a.refreshRemeshState(&bim)
	if bim.RemeshStatus != "succeeded" {
		fail(c, http.StatusConflict, "请先完成 BIM 网格均匀化")
		return
	}
	meshPath := filepath.Join(bim.Dir, "mesh_remesh.ply")
	if _, err := os.Stat(meshPath); err != nil {
		fail(c, http.StatusConflict, "均匀化结果文件不存在，请重新执行")
		return
	}
	scanPath, err := a.resolveScanSourcePath(scan, userID(c))
	if err != nil {
		fail(c, http.StatusNotFound, "点云源文件不存在")
		return
	}
	var alignment DBAlignment
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, userID(c)).First(&alignment).Error; err != nil {
		fail(c, http.StatusNotFound, "请先保存粗配准矩阵")
		return
	}
	var matrix []float64
	if json.Unmarshal([]byte(alignment.MatrixJSON), &matrix) != nil || len(matrix) != 16 {
		fail(c, http.StatusInternalServerError, "配准矩阵格式非法")
		return
	}
	if err := normalizeC2MRequest(&req); err != nil {
		fail(c, http.StatusBadRequest, err.Error())
		return
	}
	inputFingerprint, err := c2mInputFingerprint(alignment.MatrixJSON, scanPath, meshPath)
	if err != nil {
		fail(c, http.StatusConflict, "C2M 输入文件不可用")
		return
	}
	serviceParams := c2mServiceParams(req)
	paramsJSON, _ := json.Marshal(serviceParams)
	body, _ := json.Marshal(map[string]any{"scan_path": meshServicePath(a.cfg.DataDir, scanPath, a.cfg.MeshServiceStorageDir), "mesh_path": meshServicePath(a.cfg.DataDir, meshPath, a.cfg.MeshServiceStorageDir), "alignment_matrix": matrix, "params": serviceParams})
	request, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, strings.TrimRight(a.cfg.MeshServiceURL, "/")+"/c2m/compute", bytes.NewReader(body))
	if err != nil {
		fail(c, 500, "构建 C2M 请求失败")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 30 * time.Minute}).Do(request)
	if err != nil {
		fail(c, 502, "调用 C2M 计算服务失败")
		return
	}
	defer resp.Body.Close()
	responseBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		detail := meshServiceError(responseBody)
		log.Printf("C2M 计算服务返回错误: status=%d detail=%s", resp.StatusCode, detail)
		message := fmt.Sprintf("C2M 计算服务返回错误(%d)", resp.StatusCode)
		if detail != "" {
			message += ": " + detail
		}
		status := http.StatusBadGateway
		if resp.StatusCode == http.StatusTooManyRequests {
			copyRetryAfter(c.Writer.Header(), resp)
			status = http.StatusTooManyRequests
		} else if resp.StatusCode == http.StatusNotImplemented {
			status = http.StatusNotImplemented
		}
		fail(c, status, message)
		return
	}
	var result c2mServiceResult
	if json.Unmarshal(responseBody, &result) != nil {
		fail(c, 502, "解析 C2M 结果失败")
		return
	}
	coloredPath, distancesPath := backendDataPath(a.cfg.DataDir, result.ColoredPlyPath, a.cfg.MeshServiceStorageDir), backendDataPath(a.cfg.DataDir, result.DistancesPath, a.cfg.MeshServiceStorageDir)
	coloredPath, coloredErr := a.c2mArtifactPath(coloredPath)
	distancesPath, distancesErr := a.c2mArtifactPath(distancesPath)
	if coloredErr != nil || distancesErr != nil || !regularFileExists(coloredPath) || !regularFileExists(distancesPath) {
		a.removeC2MArtifact(coloredPath)
		a.removeC2MArtifact(distancesPath)
		fail(c, http.StatusBadGateway, "C2M 服务返回了无效的产物路径")
		return
	}
	profile, err := resolveC2MResultProfile(req.Profile, result.Profile)
	if err != nil {
		a.removeC2MArtifact(coloredPath)
		a.removeC2MArtifact(distancesPath)
		fail(c, http.StatusBadGateway, err.Error())
		return
	}
	currentFingerprint, fingerprintErr := a.currentC2MInputFingerprint(scan.ID, bim.ID, userID(c))
	if fingerprintErr != nil || currentFingerprint != inputFingerprint {
		a.removeC2MArtifact(coloredPath)
		a.removeC2MArtifact(distancesPath)
		fail(c, http.StatusConflict, "计算期间配准或网格输入发生变化，请重新计算")
		return
	}
	row := DBC2MResult{ScanID: scan.ID, BimID: bim.ID, OwnerID: userID(c), PointsBefore: result.PointsBefore, PointsAfter: result.PointsAfter, MeshVertexCount: result.MeshVertices, VoxelSize: req.VoxelSize, MaxColormapDistance: req.MaxColormapDistance, MaxHistogramDistance: req.MaxHistogramDistance, HistogramBins: req.HistogramBins, ToleranceLimit: req.ToleranceLimit, InputFingerprint: inputFingerprint, ParamsJSON: string(paramsJSON), MinDist: result.Stats.Min, MeanDist: result.Stats.Mean, StdDist: result.Stats.Std, P50: result.Stats.P50, P90: result.Stats.P90, P95: result.Stats.P95, P99: result.Stats.P99, MaxDist: result.Stats.Max, MeanAbs: result.Stats.MeanAbs, RMSE: result.Stats.RMSE, P95Abs: result.Stats.P95Abs, WithinToleranceRatio: result.Stats.WithinToleranceRatio, Profile: profile, AlgorithmVersion: result.AlgorithmVersion, MetricDirection: result.MetricDirection, ApproximationJSON: string(result.Approximation), HistogramJSON: string(result.Histogram), DiagnosticsJSON: string(result.Diagnostics), ColoredPlyPath: coloredPath, DistancesPath: distancesPath}
	var previous DBC2MResult
	previousFound := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, userID(c)).First(&previous).Error == nil
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scan.ID, bim.ID, userID(c)).Assign(row).FirstOrCreate(&row).Error; err != nil {
		a.removeC2MArtifact(coloredPath)
		a.removeC2MArtifact(distancesPath)
		fail(c, 500, "保存 C2M 结果失败")
		return
	}
	if previousFound {
		if previous.ColoredPlyPath != coloredPath {
			a.removeC2MArtifact(previous.ColoredPlyPath)
		}
		if previous.DistancesPath != distancesPath {
			a.removeC2MArtifact(previous.DistancesPath)
		}
	}
	ok(c, a.c2mResultResponse(row))
}

func (a *app) getC2MLatest(c *gin.Context) {
	scanID, _ := strconv.ParseInt(c.Query("modelScanFileId"), 10, 64)
	bimID, _ := strconv.ParseInt(c.Query("modelBimFileId"), 10, 64)
	var row DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, userID(c)).First(&row).Error; err != nil {
		fail(c, 404, "暂无 C2M 计算结果")
		return
	}
	ok(c, a.c2mResultResponse(row))
}

func (a *app) c2mColoredPly(c *gin.Context) {
	scanID, _ := strconv.ParseInt(c.Query("modelScanFileId"), 10, 64)
	bimID, _ := strconv.ParseInt(c.Query("modelBimFileId"), 10, 64)
	var row DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, userID(c)).First(&row).Error; err != nil || row.ColoredPlyPath == "" {
		fail(c, 404, "暂无 C2M 着色结果")
		return
	}
	if fresh, reason := a.c2mFreshness(row); !fresh {
		fail(c, http.StatusConflict, reason)
		return
	}
	path, err := a.c2mArtifactPath(row.ColoredPlyPath)
	if err != nil || !regularFileExists(path) {
		fail(c, http.StatusNotFound, "C2M 着色文件不存在")
		return
	}
	c.Header("Content-Disposition", `inline; filename="c2m_colored.ply"`)
	c.File(path)
}

func (a *app) c2mDistances(c *gin.Context) {
	scanID, _ := strconv.ParseInt(c.Query("modelScanFileId"), 10, 64)
	bimID, _ := strconv.ParseInt(c.Query("modelBimFileId"), 10, 64)
	var row DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", scanID, bimID, userID(c)).First(&row).Error; err != nil || row.DistancesPath == "" {
		fail(c, http.StatusNotFound, "暂无 C2M 距离数据")
		return
	}
	if fresh, reason := a.c2mFreshness(row); !fresh {
		fail(c, http.StatusConflict, reason)
		return
	}
	path, err := a.c2mArtifactPath(row.DistancesPath)
	if err != nil || !regularFileExists(path) {
		fail(c, http.StatusNotFound, "C2M 距离文件不存在")
		return
	}
	c.Header("Content-Type", "application/octet-stream")
	c.Header("Content-Disposition", `attachment; filename="c2m_distances.bin"`)
	c.File(path)
}

func (a *app) recolorC2M(c *gin.Context) {
	var req c2mRecolorRequest
	if c.ShouldBindJSON(&req) != nil || req.ModelScanFileID <= 0 || req.ModelBimFileID <= 0 {
		fail(c, http.StatusBadRequest, "Scan 与 BIM 资产 ID 非法")
		return
	}
	if err := normalizeC2MRecolorRequest(&req); err != nil {
		fail(c, http.StatusBadRequest, err.Error())
		return
	}

	var row DBC2MResult
	if err := a.db.Where("scan_id = ? AND bim_id = ? AND owner_id = ?", req.ModelScanFileID, req.ModelBimFileID, userID(c)).First(&row).Error; err != nil {
		fail(c, http.StatusNotFound, "暂无 C2M 计算结果")
		return
	}
	if fresh, reason := a.c2mFreshness(row); !fresh {
		fail(c, http.StatusConflict, reason)
		return
	}
	distancesPath, err := a.c2mArtifactPath(row.DistancesPath)
	if err != nil || !regularFileExists(distancesPath) {
		fail(c, http.StatusNotFound, "C2M 距离文件不存在，请重新计算")
		return
	}
	var bim DBAsset
	if err := a.db.Where("id = ? AND owner_id = ? AND type = ? AND status = ?", req.ModelBimFileID, userID(c), "bim", "ready").First(&bim).Error; err != nil {
		fail(c, http.StatusNotFound, "BIM 资产不可用")
		return
	}
	meshPath := filepath.Join(bim.Dir, "mesh_remesh.ply")
	if !regularFileExists(meshPath) {
		fail(c, http.StatusConflict, "均匀化结果文件不存在，请重新执行")
		return
	}

	body, _ := json.Marshal(map[string]any{
		"distances_path":         meshServicePath(a.cfg.DataDir, distancesPath, a.cfg.MeshServiceStorageDir),
		"mesh_path":              meshServicePath(a.cfg.DataDir, meshPath, a.cfg.MeshServiceStorageDir),
		"max_colormap_distance":  req.MaxColormapDistance,
		"max_histogram_distance": req.MaxHistogramDistance,
		"histogram_bins":         req.HistogramBins,
		"tolerance_limit":        req.ToleranceLimit,
		"smoothing_iterations":   0,
		"smoothing_strength":     0.5,
	})
	request, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, strings.TrimRight(a.cfg.MeshServiceURL, "/")+"/c2m/recolor", bytes.NewReader(body))
	if err != nil {
		fail(c, http.StatusInternalServerError, "构建 C2M 重着色请求失败")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 30 * time.Minute}).Do(request)
	if err != nil {
		fail(c, http.StatusBadGateway, "调用 C2M 重着色服务失败")
		return
	}
	defer resp.Body.Close()
	responseBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		if resp.StatusCode == http.StatusTooManyRequests {
			copyRetryAfter(c.Writer.Header(), resp)
			fail(c, http.StatusTooManyRequests, "C2M 服务正忙，请稍后重试")
			return
		}
		detail := meshServiceError(responseBody)
		if detail == "" {
			detail = "C2M 重着色服务返回错误"
		}
		fail(c, http.StatusBadGateway, detail)
		return
	}
	var result c2mRecolorServiceResult
	if err := json.Unmarshal(responseBody, &result); err != nil || !json.Valid(result.Histogram) {
		fail(c, http.StatusBadGateway, "解析 C2M 重着色结果失败")
		return
	}
	newColoredPath := backendDataPath(a.cfg.DataDir, result.ColoredPlyPath, a.cfg.MeshServiceStorageDir)
	newColoredPath, err = a.c2mArtifactPath(newColoredPath)
	if err != nil || !regularFileExists(newColoredPath) {
		fail(c, http.StatusBadGateway, "C2M 重着色服务返回了无效的产物路径")
		return
	}
	if fresh, reason := a.c2mFreshness(row); !fresh {
		a.removeC2MArtifact(newColoredPath)
		fail(c, http.StatusConflict, reason)
		return
	}

	oldColoredPath := row.ColoredPlyPath
	row.MaxColormapDistance = req.MaxColormapDistance
	row.MaxHistogramDistance = req.MaxHistogramDistance
	row.HistogramBins = req.HistogramBins
	row.ToleranceLimit = req.ToleranceLimit
	row.MinDist = result.Stats.Min
	row.MeanDist = result.Stats.Mean
	row.StdDist = result.Stats.Std
	row.P50 = result.Stats.P50
	row.P90 = result.Stats.P90
	row.P95 = result.Stats.P95
	row.P99 = result.Stats.P99
	row.MaxDist = result.Stats.Max
	row.MeanAbs = result.Stats.MeanAbs
	row.RMSE = result.Stats.RMSE
	row.P95Abs = result.Stats.P95Abs
	row.WithinToleranceRatio = result.Stats.WithinToleranceRatio
	row.HistogramJSON = string(result.Histogram)
	row.ColoredPlyPath = newColoredPath
	var params map[string]any
	if json.Unmarshal([]byte(row.ParamsJSON), &params) != nil || params == nil {
		params = map[string]any{}
	}
	params["max_colormap_distance"] = req.MaxColormapDistance
	params["max_histogram_distance"] = req.MaxHistogramDistance
	params["histogram_bins"] = req.HistogramBins
	params["tolerance_limit"] = req.ToleranceLimit
	paramsJSON, _ := json.Marshal(params)
	row.ParamsJSON = string(paramsJSON)
	if err := a.db.Save(&row).Error; err != nil {
		a.removeC2MArtifact(newColoredPath)
		fail(c, http.StatusInternalServerError, "保存 C2M 重着色结果失败")
		return
	}
	if oldColoredPath != newColoredPath {
		a.removeC2MArtifact(oldColoredPath)
	}
	ok(c, a.c2mResultResponse(row))
}

func (a *app) listMeasurements(c *gin.Context) {
	assetID, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, http.StatusBadRequest, "资产 ID 无效")
		return
	}
	var asset DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", assetID, userID(c)).First(&asset).Error; err != nil {
		fail(c, http.StatusNotFound, "资产不存在")
		return
	}
	var rows []DBMeasurement
	if err := a.db.Where("asset_id = ? AND owner_id = ?", assetID, userID(c)).Order("created_at asc").Find(&rows).Error; err != nil {
		fail(c, 500, "查询测量记录失败")
		return
	}
	result := make([]gin.H, 0, len(rows))
	for _, row := range rows {
		var payload any
		if json.Unmarshal([]byte(row.Payload), &payload) != nil {
			continue
		}
		result = append(result, gin.H{"id": row.ID, "kind": row.Kind, "payload": payload, "createdAt": row.CreatedAt})
	}
	ok(c, result)
}

func (a *app) createMeasurement(c *gin.Context) {
	assetID, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, 400, "资产 ID 无效")
		return
	}
	var asset DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", assetID, userID(c)).First(&asset).Error; err != nil {
		fail(c, 404, "资产不存在")
		return
	}
	var req struct {
		Kind    string          `json:"kind"`
		Payload json.RawMessage `json:"payload"`
	}
	if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Kind) == "" || len(req.Payload) == 0 || !json.Valid(req.Payload) {
		fail(c, 400, "测量数据格式无效")
		return
	}
	row := DBMeasurement{AssetID: assetID, OwnerID: userID(c), Kind: strings.TrimSpace(req.Kind), Payload: string(req.Payload), CreatedAt: time.Now().UTC()}
	if err := a.db.Create(&row).Error; err != nil {
		fail(c, 500, "保存测量记录失败")
		return
	}
	ok(c, gin.H{"id": row.ID, "kind": row.Kind, "payload": json.RawMessage(row.Payload), "createdAt": row.CreatedAt})
}

func (a *app) deleteMeasurement(c *gin.Context) {
	id, err := strconv.ParseInt(c.Param("measurementId"), 10, 64)
	if err != nil {
		fail(c, 400, "测量记录 ID 无效")
		return
	}
	result := a.db.Where("id = ? AND owner_id = ?", id, userID(c)).Delete(&DBMeasurement{})
	if result.Error != nil {
		fail(c, 500, "删除测量记录失败")
		return
	}
	if result.RowsAffected == 0 {
		fail(c, 404, "测量记录不存在")
		return
	}
	ok(c, nil)
}

func (a *app) scans(c *gin.Context) {
	var rows []DBAsset
	if err := a.db.Where("owner_id = ? AND type = ?", userID(c), "pointcloud").Order("created_at DESC").Find(&rows).Error; err != nil {
		fail(c, 500, "查询扫描失败")
		return
	}
	list := []gin.H{}
	for _, item := range rows {
		var count int64
		_ = a.db.Model(&DBAlignment{}).Where("scan_id = ? AND owner_id = ?", item.ID, userID(c)).Count(&count)
		has := count > 0
		list = append(list, gin.H{"scanFileId": item.ID, "producedAt": time.Unix(item.CreatedAt, 0).UTC().Format(time.RFC3339), "hasCadAlignment": false, "hasBimAlignment": has, "calibrated": has})
	}
	ok(c, gin.H{"total": len(list), "page": 1, "pageSize": len(list), "list": list})
}
func (a *app) calibration(c *gin.Context) {
	id, _ := strconv.ParseInt(c.Param("id"), 10, 64)
	var al DBAlignment
	_ = a.db.Where("scan_id = ? AND owner_id = ?", id, userID(c)).First(&al).Error
	var bim any
	if al.ID > 0 {
		bim = al.BimID
	}
	ok(c, gin.H{"scanFileId": id, "calibrated": bim != nil, "hasCadAlignment": false, "hasBimAlignment": bim != nil, "hasGaussBinding": false, "cadFileId": nil, "bimFileId": bim, "gaussFileId": nil})
}

func requestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := strings.TrimSpace(c.GetHeader("X-Request-ID"))
		if id == "" || len(id) > 128 {
			id = randomID()
		}
		c.Header("X-Request-ID", id)
		c.Set("requestID", id)
		c.Next()
	}
}

func (a *app) health(c *gin.Context) {
	status := http.StatusOK
	data := gin.H{"service": "cloudbim-viewer-backend", "time": time.Now().UTC(), "database": "ready"}
	if sqlDB, err := a.db.DB(); err != nil || sqlDB.PingContext(c.Request.Context()) != nil {
		status = http.StatusServiceUnavailable
		data["database"] = "unavailable"
	}
	c.JSON(status, response{Code: status, Msg: http.StatusText(status), Data: data})
}

func main() {
	// .env is a local-development convenience; production uses injected env only.
	if strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV"))) != "production" {
		if err := godotenv.Load(); err != nil && !os.IsNotExist(err) {
			log.Printf("读取 .env 失败，将继续使用系统环境变量: %v", err)
		}
	}
	cfg, err := loadConfig()
	if err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(cfg.DataDir, "uploads"), 0755); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(cfg.DataDir, "assets"), 0755); err != nil {
		log.Fatal(err)
	}
	a := newApp(cfg)
	if err := a.connectDB(); err != nil {
		log.Fatal(err)
	}
	serverContext, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := a.startWorkers(serverContext); err != nil {
		log.Fatal(err)
	}
	r := gin.New()
	corsConfig := cors.Config{AllowMethods: []string{"GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"}, AllowHeaders: []string{"Origin", "Content-Type", "Authorization", "Tus-Resumable", "Upload-Length", "Upload-Metadata", "Upload-Offset", "X-Request-ID"}, ExposeHeaders: []string{"Location", "Upload-Length", "Upload-Offset", "Tus-Resumable", "X-Request-ID", "ETag", "Last-Modified", "Accept-Ranges", "Content-Range", "Retry-After"}, AllowCredentials: true, MaxAge: 12 * time.Hour}
	if len(cfg.CORSAllowOrigins) == 1 && cfg.CORSAllowOrigins[0] == "*" {
		corsConfig.AllowAllOrigins = true
		corsConfig.AllowCredentials = false
	} else {
		corsConfig.AllowOrigins = cfg.CORSAllowOrigins
	}
	r.Use(gin.Logger(), gin.Recovery(), requestID(), cors.New(corsConfig))
	r.GET("/health", a.health)
	auth := r.Group("/auth")
	auth.POST("/register", a.register)
	auth.POST("/login", a.login)
	auth.GET("/me", a.authRequired(), a.me)
	r.Use(a.authRequired())
	r.POST("/uploads", a.createUpload)
	r.GET("/uploads/:id", a.uploadStatus)
	r.HEAD("/uploads/:id", a.upload)
	r.PATCH("/uploads/:id", a.upload)
	r.DELETE("/uploads/:id", a.upload)
	r.GET("/assets", a.listAssets)
	r.GET("/assets/:id", a.assetDetail)
	r.GET("/assets/:id/representations", a.assetRepresentations)
	r.GET("/assets/:id/representations/:kind/:version/*path", a.derivativeResource)
	r.HEAD("/assets/:id/representations/:kind/:version/*path", a.derivativeResource)
	r.PATCH("/assets/:id/appearance", a.updateAssetAppearance)
	r.DELETE("/assets/:id", a.deleteAsset)
	r.GET("/assets/:id/measurements", a.listMeasurements)
	r.POST("/assets/:id/measurements", a.createMeasurement)
	r.GET("/assets/:id/:resource", a.resource)
	r.HEAD("/assets/:id/:resource", a.resource)
	r.DELETE("/measurements/:measurementId", a.deleteMeasurement)
	r.GET("/mesh/algorithms", a.meshAlgorithms)
	r.POST("/assets/:id/mesh/remesh", a.remeshAsset)
	r.GET("/assets/:id/mesh/remesh/status", a.remeshStatus)
	r.GET("/assets/:id/mesh/remesh/latest", a.remeshLatest)
	r.GET("/assets/:id/tiles/*path", a.tile)
	r.HEAD("/assets/:id/tiles/*path", a.tile)
	r.GET("/scans", a.scans)
	r.GET("/scans/:id/calibration", a.calibration)
	r.POST("/alignments/bim", a.createAlignment)
	r.GET("/alignments/bim", a.getAlignment)
	r.POST("/alignments/bim/fine", a.fineAlignment)
	r.POST("/alignments/bim/c2m", a.computeC2M)
	r.POST("/alignments/bim/c2m/recolor", a.recolorC2M)
	r.GET("/alignments/bim/c2m/latest", a.getC2MLatest)
	r.GET("/alignments/bim/c2m/colored-ply", a.c2mColoredPly)
	r.GET("/alignments/bim/c2m/distances", a.c2mDistances)
	server := &http.Server{Addr: cfg.Addr, Handler: r, ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 15 * time.Minute, WriteTimeout: 2 * time.Hour, IdleTimeout: 120 * time.Second, MaxHeaderBytes: 1 << 20}
	go func() {
		log.Printf("cloudBIM backend listening on %s, data=%s, workers=%d", cfg.Addr, cfg.DataDir, cfg.WorkerCount)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("HTTP 服务异常退出: %v", err)
			stop()
		}
	}()
	<-serverContext.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Printf("HTTP 服务优雅停机失败: %v", err)
	}
	a.waitWorkers()
	if sqlDB, err := a.db.DB(); err == nil {
		_ = sqlDB.Close()
	}
}
