package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
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
	ID           int64   `json:"id"`
	Type         string  `json:"type"`
	SourceName   string  `json:"sourceName"`
	SourceSize   int64   `json:"sourceSize"`
	Status       string  `json:"status"`
	ErrorMessage *string `json:"errorMessage"`
	CreatedAt    int64   `json:"createdAt"`
	OwnerID      int64   `json:"-"`
	Dir          string  `json:"-"`
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
	ModelID     int64     `json:"modelId"`
	ScanID      int64     `json:"modelScanFileId"`
	BimID       int64     `json:"modelBimFileId"`
	Qx          float64   `json:"modelRotationQx"`
	Qy          float64   `json:"modelRotationQy"`
	Qz          float64   `json:"modelRotationQz"`
	Qw          float64   `json:"modelRotationQw"`
	Tx          float64   `json:"modelTranslationX"`
	Ty          float64   `json:"modelTranslationY"`
	Tz          float64   `json:"modelTranslationZ"`
	Matrix      []float64 `json:"modelMatrix"`
	RMSE        float64   `json:"modelRmse"`
	MaxError    float64   `json:"modelMaxError"`
	PairCount   int       `json:"modelPairCount"`
	InlierCount int       `json:"modelInlierCount"`
}
type DBUser struct {
	ID           int64  `gorm:"primaryKey"`
	Username     string `gorm:"size:128;uniqueIndex;not null"`
	PasswordHash string `gorm:"size:255;not null"`
	CreatedAt    time.Time
}
type DBAsset struct {
	ID           int64   `gorm:"primaryKey"`
	Type         string  `gorm:"size:32;index;not null"`
	SourceName   string  `gorm:"size:255;not null"`
	SourceSize   int64   `gorm:"not null"`
	Status       string  `gorm:"size:32;index;not null"`
	ErrorMessage *string `gorm:"type:text"`
	CreatedAt    int64   `gorm:"index;not null"`
	OwnerID      int64   `gorm:"index;not null"`
	Dir          string  `gorm:"size:1024;not null"`
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

type config struct {
	Addr             string
	DataDir          string
	JWTSecret        string
	JWTExpiresIn     time.Duration
	MeshServiceURL   string
	RegisterCode     string
	Environment      string
	DBDriver         string
	DBDSN            string
	CORSAllowOrigins []string
	SeedDemo         bool
	WorkerCount      int
	UploadChunkLimit int64
	UploadFileLimit  int64
	ShutdownTimeout  time.Duration
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
	shutdownSeconds, _ := strconv.Atoi(env("SHUTDOWN_TIMEOUT_SECONDS", "15"))
	if shutdownSeconds < 1 || shutdownSeconds > 300 {
		shutdownSeconds = 15
	}
	return config{Addr: env("ADDR", ":8090"), DataDir: root, JWTSecret: secret, JWTExpiresIn: jwtExpiresIn, MeshServiceURL: strings.TrimRight(env("MESH_SERVICE_URL", ""), "/"), RegisterCode: env("REGISTER_CODE", "laochen"), Environment: environment, DBDriver: dbDriver, DBDSN: dsn, CORSAllowOrigins: origins, SeedDemo: seedDemo, WorkerCount: workers, UploadChunkLimit: chunkLimit, UploadFileLimit: fileLimit, ShutdownTimeout: time.Duration(shutdownSeconds) * time.Second}, nil
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
	mu       sync.RWMutex
	uploadMu sync.Mutex
	db       *gorm.DB
	cfg      config
	jobs     chan string
	workerWG sync.WaitGroup
}

func newApp(cfg config) *app {
	return &app{cfg: cfg, jobs: make(chan string, cfg.WorkerCount*4)}
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
	if err := db.AutoMigrate(&DBUser{}, &DBAsset{}, &DBUpload{}, &DBAlignment{}); err != nil {
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

func (a *app) ensureAssetArtifacts(asset *DBAsset) {
	if asset.Status != "ready" || validAssetArtifacts(*asset) {
		return
	}
	asset.Status = "failed"
	message := "转换产物缺失或无效，请重新上传原始文件"
	asset.ErrorMessage = &message
	_ = a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Updates(map[string]any{"status": asset.Status, "error_message": message}).Error
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
	result := gin.H{"uploadId": up.ID, "assetId": nil, "assetType": up.AssetType, "fileName": up.FileName, "fileSize": up.FileSize, "uploadOffset": up.Offset, "uploadLength": up.FileSize, "status": up.Status, "errorMessage": up.ErrorMessage}
	if up.AssetID > 0 {
		result["assetId"] = up.AssetID
	}
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
		err = buildPointCloud(ctx, source, asset.Dir)
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
		if updateErr := a.db.Model(&DBAsset{}).Where("id = ?", asset.ID).Update("status", "ready").Error; updateErr != nil {
			log.Printf("更新资产 %d 就绪状态失败: %v", asset.ID, updateErr)
		}
		if updateErr := a.db.Model(&DBUpload{}).Where("id = ?", uploadID).Update("status", "ready").Error; updateErr != nil {
			log.Printf("更新上传 %s 就绪状态失败: %v", uploadID, updateErr)
		}
	}
}

func (a *app) enqueue(uploadID string) {
	a.jobs <- uploadID
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
	return nil
}

func (a *app) waitWorkers() {
	a.workerWG.Wait()
}
func assetSummary(a Asset) gin.H {
	return gin.H{"id": a.ID, "type": a.Type, "sourceName": a.SourceName, "sourceSize": a.SourceSize, "status": a.Status, "errorMessage": a.ErrorMessage, "createdAt": a.CreatedAt}
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
		wasReady := item.Status == "ready"
		a.ensureAssetArtifacts(&item)
		if wasReady && item.Status != "ready" && status == "ready" {
			total--
			continue
		}
		list = append(list, assetSummary(Asset{ID: item.ID, Type: item.Type, SourceName: item.SourceName, SourceSize: item.SourceSize, Status: item.Status, ErrorMessage: item.ErrorMessage, CreatedAt: item.CreatedAt, OwnerID: item.OwnerID, Dir: item.Dir}))
	}
	ok(c, gin.H{"total": total, "page": page, "pageSize": size, "list": list})
}
func (a *app) getAsset(c *gin.Context) (*Asset, bool) {
	id, _ := strconv.ParseInt(c.Param("id"), 10, 64)
	var row DBAsset
	if err := a.db.Where("id = ? AND owner_id = ?", id, userID(c)).First(&row).Error; err != nil {
		return nil, false
	}
	a.ensureAssetArtifacts(&row)
	item := Asset{ID: row.ID, Type: row.Type, SourceName: row.SourceName, SourceSize: row.SourceSize, Status: row.Status, ErrorMessage: row.ErrorMessage, CreatedAt: row.CreatedAt, OwnerID: row.OwnerID, Dir: row.Dir}
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
	if err := a.db.Delete(&DBAsset{}, "id = ? AND owner_id = ?", item.ID, userID(c)).Error; err != nil {
		fail(c, 500, "删除资产失败")
		return
	}
	_ = os.RemoveAll(item.Dir)
	ok(c, nil)
}
func (a *app) resource(c *gin.Context) {
	item, found := a.getAsset(c)
	if !found || item.Status != "ready" {
		fail(c, 404, "资源不存在或尚未就绪")
		return
	}
	switch c.Param("resource") {
	case "glb":
		if item.Type != "bim" {
			fail(c, 404, "资源不存在")
			return
		}
		c.File(filepath.Join(item.Dir, "model.glb"))
	case "metadata":
		if item.Type != "bim" {
			fail(c, 404, "资源不存在")
			return
		}
		c.File(filepath.Join(item.Dir, "metadata.json"))
	default:
		fail(c, 404, "资源不存在")
	}
}
func (a *app) tile(c *gin.Context) {
	item, found := a.getAsset(c)
	if !found || item.Type != "pointcloud" || item.Status != "ready" {
		fail(c, 404, "资源不存在")
		return
	}
	path, err := safeJoin(filepath.Join(item.Dir, "tiles"), c.Param("path"))
	if err != nil {
		fail(c, 400, err.Error())
		return
	}
	c.File(path)
}

func buildBIM(parent context.Context, source, dir string) error {
	// Tus stores the uploaded payload as a normalized `source` path without an
	// extension. The asset type and original extension are validated at upload
	// creation, so checking filepath.Ext(source) here would reject valid files.
	tool, err := resolveTool("IFC_BUNDLE_BIN", "ifc_bundle", filepath.Join("..", "..", "zhongjian-back", "tools", "ifc_bundle", "ifc_bundle"))
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(parent, 45*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, tool, "--input", source, "--glb-out", filepath.Join(dir, "model.glb"), "--meta-out", filepath.Join(dir, "metadata.json"))
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
func buildPointCloud(parent context.Context, source, dir string) error {
	// See buildBIM: the normalized Tus source intentionally has no extension.
	tool, err := resolveTool("GOCESIUMTILER_BIN", "gocesiumtiler", filepath.Join("..", "..", "zhongjian-back", "tools", "gocesiumtiler", "gocesiumtiler-lin-x64"))
	if err != nil {
		return err
	}
	tilesDir := filepath.Join(dir, "tiles")
	ctx, cancel := context.WithTimeout(parent, 60*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, tool, "file", "--coord-mode", "source", "--sample-mode", "min-distance", "--sample-distance", "0.03", "-o", tilesDir, source)
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

func resolveTool(envName, command, fallback string) (string, error) {
	if value := strings.TrimSpace(os.Getenv(envName)); value != "" {
		if _, err := os.Stat(value); err != nil {
			return "", fmt.Errorf("工具不存在: %s", value)
		}
		return value, nil
	}
	if value, err := exec.LookPath(command); err == nil {
		return value, nil
	}
	if _, err := os.Stat(fallback); err == nil {
		return fallback, nil
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
	if _, ok := a.getAssetByID(c, req.ModelScanFileID, "pointcloud"); !ok {
		fail(c, 404, "点云资产不存在")
		return
	}
	if _, ok := a.getAssetByID(c, req.ModelBimFileID, "bim"); !ok {
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
	result := Alignment{ModelID: 0, ScanID: req.ModelScanFileID, BimID: req.ModelBimFileID, Qx: qx, Qy: qy, Qz: qz, Qw: qw, Tx: trX, Ty: trY, Tz: trZ, Matrix: matrix, RMSE: rmse, MaxError: max, PairCount: fit.pairCount, InlierCount: fit.inlierCount}
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
	return Asset{ID: row.ID, Type: row.Type, SourceName: row.SourceName, SourceSize: row.SourceSize, Status: row.Status, ErrorMessage: row.ErrorMessage, CreatedAt: row.CreatedAt, OwnerID: row.OwnerID, Dir: row.Dir}, true
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
	ok(c, Alignment{ModelID: row.ID, ScanID: row.ScanID, BimID: row.BimID, Qx: row.Qx, Qy: row.Qy, Qz: row.Qz, Qw: row.Qw, Tx: row.Tx, Ty: row.Ty, Tz: row.Tz, Matrix: matrix, RMSE: row.RMSE, MaxError: row.MaxError, PairCount: row.PairCount, InlierCount: row.InlierCount})
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
	ModelScanFileID     int64                `json:"modelScanFileId"`
	ModelBimFileID      int64                `json:"modelBimFileId"`
	ModelMatrix         []float64            `json:"modelMatrix"`
	Fallback            bool                 `json:"fallback"`
	Regressed           bool                 `json:"regressed"`
	AppliedFineResult   bool                 `json:"appliedFineResult"`
	Metrics             fineAlignmentMetrics `json:"metrics"`
	RMSERegressRatio    float64              `json:"rmseRegressRatio"`
	FitnessRegressRatio float64              `json:"fitnessRegressRatio"`
	ApplyWhenRegressed  bool                 `json:"applyWhenRegressed"`
	ModelRotationQx     float64              `json:"modelRotationQx"`
	ModelRotationQy     float64              `json:"modelRotationQy"`
	ModelRotationQz     float64              `json:"modelRotationQz"`
	ModelRotationQw     float64              `json:"modelRotationQw"`
	ModelTranslationX   float64              `json:"modelTranslationX"`
	ModelTranslationY   float64              `json:"modelTranslationY"`
	ModelTranslationZ   float64              `json:"modelTranslationZ"`
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
	var upload DBUpload
	if err := a.db.Where("asset_id = ? AND owner_id = ?", scan.ID, userID(c)).Order("created_at DESC").First(&upload).Error; err != nil {
		fail(c, 404, "点云源文件不存在")
		return
	}
	sourcePath := filepath.Join(upload.Dir, "source")
	if _, err := os.Stat(sourcePath); err != nil {
		fail(c, 404, "点云源文件不存在")
		return
	}
	meshPath := ""
	for _, candidate := range []string{"mesh_remesh.ply", "mesh.ply", "model.ply"} {
		path := filepath.Join(bim.Dir, candidate)
		if _, err := os.Stat(path); err == nil {
			meshPath = path
			break
		}
	}
	if meshPath == "" {
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
	body, err := json.Marshal(map[string]any{"scan_path": sourcePath, "mesh_path": meshPath, "init_transform": initialMatrix, "max_correspondence_distance": req.MaxCorrespondenceDistance, "rmse_regress_ratio": req.RMSERegressRatio, "fitness_regress_ratio": req.FitnessRegressRatio, "apply_when_regressed": req.ApplyWhenRegressed})
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
		fail(c, 502, fmt.Sprintf("精细化配准服务返回错误(%d): %s", resp.StatusCode, toolLog(responseBody)))
		return
	}
	var result fineAlignmentResponse
	if err := json.Unmarshal(responseBody, &result); err != nil {
		fail(c, 502, "解析精细化配准结果失败")
		return
	}
	result.ModelScanFileID, result.ModelBimFileID = scan.ID, bim.ID
	result.RMSERegressRatio, result.FitnessRegressRatio, result.ApplyWhenRegressed = req.RMSERegressRatio, req.FitnessRegressRatio, req.ApplyWhenRegressed
	ok(c, result)
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
	corsConfig := cors.Config{AllowMethods: []string{"GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"}, AllowHeaders: []string{"Origin", "Content-Type", "Authorization", "Tus-Resumable", "Upload-Length", "Upload-Metadata", "Upload-Offset", "X-Request-ID"}, ExposeHeaders: []string{"Location", "Upload-Length", "Upload-Offset", "Tus-Resumable", "X-Request-ID"}, AllowCredentials: true, MaxAge: 12 * time.Hour}
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
	r.DELETE("/assets/:id", a.deleteAsset)
	r.GET("/assets/:id/:resource", a.resource)
	r.GET("/assets/:id/tiles/*path", a.tile)
	r.GET("/scans", a.scans)
	r.GET("/scans/:id/calibration", a.calibration)
	r.POST("/alignments/bim", a.createAlignment)
	r.GET("/alignments/bim", a.getAlignment)
	r.POST("/alignments/bim/fine", a.fineAlignment)
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
