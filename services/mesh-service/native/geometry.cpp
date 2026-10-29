// Exact finite geometry kernels. No relaxed floating-point or geometric gates.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numeric>
#include <vector>

using Vec = std::array<double, 3>;
static Vec sub(const Vec& a, const Vec& b) { return {a[0]-b[0], a[1]-b[1], a[2]-b[2]}; }
static double dot(const Vec& a, const Vec& b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
static Vec read(const double* p) { return {p[0],p[1],p[2]}; }
struct Triangle { Vec a,b,c,n,partition,center; };
struct Node {
    Vec lo,hi,normal;
    double radius;
    int left=-1,right=-1;
    std::vector<int64_t> ids;
};

struct SurfaceIndex {
    std::vector<Triangle> triangles;
    std::vector<Node> nodes;
    SurfaceIndex(const double* xyz, const double* normals, const double* partition,
                 const int64_t* valid, int64_t count, int64_t valid_count) {
        triangles.reserve(count);
        for (int64_t i=0;i<count;++i) {
            Triangle t{read(xyz+9*i),read(xyz+9*i+3),read(xyz+9*i+6),
                       read(normals+3*i),read(partition+3*i),{}};
            for (int k=0;k<3;++k) t.center[k]=(t.a[k]+t.b[k]+t.c[k])/3.;
            triangles.push_back(t);
        }
        nodes.reserve(2*valid_count/16+1);
        build(std::vector<int64_t>(valid,valid+valid_count));
    }
    int build(std::vector<int64_t> ids) {
        int index=nodes.size(); nodes.emplace_back();
        Node node;
        node.lo.fill(std::numeric_limits<double>::infinity());
        node.hi.fill(-std::numeric_limits<double>::infinity());
        node.normal.fill(0.); node.radius=0.;
        Vec center_lo=node.lo,center_hi=node.hi;
        for (auto id:ids) {
            const auto& t=triangles[id];
            for (int k=0;k<3;++k) {
                node.lo[k]=std::min({node.lo[k],t.a[k],t.b[k],t.c[k]});
                node.hi[k]=std::max({node.hi[k],t.a[k],t.b[k],t.c[k]});
                node.normal[k]+=t.partition[k];
                center_lo[k]=std::min(center_lo[k],t.center[k]);
                center_hi[k]=std::max(center_hi[k],t.center[k]);
            }
        }
        if (!ids.empty()) for (auto& value:node.normal) value/=ids.size();
        for (auto id:ids) { auto delta=sub(triangles[id].partition,node.normal); node.radius=std::max(node.radius,std::sqrt(dot(delta,delta))); }
        if (ids.size()>32) {
            int axis=0;
            for (int k=1;k<3;++k) if (center_hi[k]-center_lo[k]>center_hi[axis]-center_lo[axis]) axis=k;
            std::stable_sort(ids.begin(),ids.end(),[&](auto a,auto b){return triangles[a].center[axis]<triangles[b].center[axis];});
            auto middle=ids.begin()+ids.size()/2;
            node.left=build(std::vector<int64_t>(ids.begin(),middle));
            node.right=build(std::vector<int64_t>(middle,ids.end()));
        } else {
            std::sort(ids.begin(),ids.end()); node.ids=std::move(ids);
        }
        nodes[index]=std::move(node); return index;
    }
    static double distance(const Vec& p,const Triangle& t) {
        auto ab=sub(t.b,t.a),ac=sub(t.c,t.a),d=sub(p,t.a);
        double height=dot(d,t.n); Vec projected;
        for(int k=0;k<3;++k) projected[k]=d[k]-height*t.n[k];
        double aa=dot(ab,ab),cc=dot(ac,ac),bc=dot(ab,ac);
        double u=dot(projected,ab),v=dot(projected,ac),det=std::max(aa*cc-bc*bc,1e-30);
        double beta=(u*cc-v*bc)/det,gamma=(v*aa-u*bc)/det;
        double best=(beta>=0 && gamma>=0 && beta+gamma<=1)?height*height:std::numeric_limits<double>::infinity();
        std::array<Vec,3> vertices{t.a,t.b,t.c};
        for (int j=0;j<3;++j) {
            auto edge=sub(vertices[(j+1)%3],vertices[j]),relative=sub(p,vertices[j]);
            double fraction=std::clamp(dot(relative,edge)/std::max(dot(edge,edge),1e-30),0.,1.);
            for(int k=0;k<3;++k) relative[k]-=fraction*edge[k];
            best=std::min(best,dot(relative,relative));
        }
        return std::sqrt(best);
    }
    void query(const Vec& p,const Vec& normal,int node_id,double norm_length,
               double& best,int64_t& chosen,int64_t& count) const {
        const auto& node=nodes[node_id]; Vec delta;
        for(int k=0;k<3;++k) delta[k]=std::max({node.lo[k]-p[k],p[k]-node.hi[k],0.});
        if (dot(delta,delta)>=(best+1e-12)*(best+1e-12)) return;
        // Conservative bound only; the exact normal gate is applied per face.
        if (std::abs(dot(normal,node.normal))+norm_length*node.radius+1e-14<=.75) return;
        if (node.left>=0) {
            query(p,normal,node.left,norm_length,best,chosen,count);
            query(p,normal,node.right,norm_length,best,chosen,count); return;
        }
        count+=node.ids.size();
        double distance_best=std::numeric_limits<double>::infinity(); int64_t candidate=-1;
        for(auto id:node.ids) {
            if(!(std::abs(dot(normal,triangles[id].n))>.75)) continue;
            double value=distance(p,triangles[id]);
            if(value<distance_best) { distance_best=value; candidate=id; }
        }
        if (distance_best<best-1e-12 || (std::abs(distance_best-best)<=1e-12 && chosen>=0 && candidate<chosen)) {
            best=distance_best; chosen=candidate;
        }
    }
    bool near(const Vec& p,int node_id,double square) const {
        const auto& node=nodes[node_id]; Vec delta;
        for(int k=0;k<3;++k) delta[k]=std::max({node.lo[k]-p[k],p[k]-node.hi[k],0.});
        if(dot(delta,delta)>square) return false;
        if(node.left>=0) return near(p,node.left,square) || near(p,node.right,square);
        for(auto id:node.ids) {
            const auto& t=triangles[id];
            for(int k=0;k<3;++k) delta[k]=std::max({std::min({t.a[k],t.b[k],t.c[k]})-p[k],
                                                    p[k]-std::max({t.a[k],t.b[k],t.c[k]}),0.});
            if(dot(delta,delta)<=square) return true;
        }
        return false;
    }
};

extern "C" {
void* surface_create(const double* xyz,const double* normals,const double* partition,
                     const int64_t* valid,int64_t count,int64_t valid_count) {
    try { return new SurfaceIndex(xyz,normals,partition,valid,count,valid_count); }
    catch (...) { return nullptr; }
}
void surface_destroy(void* pointer) { delete static_cast<SurfaceIndex*>(pointer); }
void surface_query(void* pointer,const double* points,const double* normals,int64_t count,
                   double tolerance,int workers,double* distance,int64_t* chosen,int64_t* operations) {
    auto* index=static_cast<SurfaceIndex*>(pointer);
    #pragma omp parallel for num_threads(workers) schedule(static)
    for(int64_t i=0;i<count;++i) {
        Vec p=read(points+3*i),n=read(normals+3*i);
        distance[i]=tolerance; chosen[i]=-1; operations[i]=0;
        index->query(p,n,0,std::sqrt(dot(n,n)),distance[i],chosen[i],operations[i]);
    }
}
void surface_near(void* pointer,const double* points,int64_t count,double tolerance,int workers,uint8_t* near) {
    auto* index=static_cast<SurfaceIndex*>(pointer);
    #pragma omp parallel for num_threads(workers) schedule(static)
    for(int64_t i=0;i<count;++i) near[i]=index->near(read(points+3*i),0,tolerance*tolerance);
}
// Direct finite-polyline projection, preserving the first segment on ties.
void polyline_project(const double* points,int64_t count,const double* starts,const double* deltas,
                      const double* squares,int64_t segments,double* best,int64_t* nearest,double* fractions) {
    for(int64_t i=0;i<count;++i) {
        Vec p=read(points+3*i); best[i]=std::numeric_limits<double>::infinity(); nearest[i]=0; fractions[i]=0.;
        for(int64_t j=0;j<segments;++j) {
            Vec a=read(starts+3*j),d=read(deltas+3*j),relative=sub(p,a);
            double t=std::clamp(dot(relative,d)/squares[j],0.,1.); Vec radial;
            for(int k=0;k<3;++k) radial[k]=p[k]-(a[k]+t*d[k]);
            double square=dot(radial,radial);
            if(square<best[i]) {best[i]=square;nearest[i]=j;fractions[i]=t;}
        }
    }
}
// The optimizer's existing expanded square-distance expression, without the
// point-by-segment temporary matrices. Keep its numerical convention intact.
void polyline_objective(const double* points,int64_t count,const double* starts,const double* deltas,
                        const double* squares,int64_t segments,double* best,int64_t* nearest,double* fractions) {
    std::vector<double> origin(segments);
    for(int64_t j=0;j<segments;++j) origin[j]=dot(read(starts+3*j),read(deltas+3*j));
    for(int64_t i=0;i<count;++i) {
        Vec p=read(points+3*i); best[i]=std::numeric_limits<double>::infinity(); nearest[i]=0; fractions[i]=0.;
        for(int64_t j=0;j<segments;++j) {
            Vec a=read(starts+3*j),d=read(deltas+3*j),relative=sub(p,a);
            double along=dot(p,d)-origin[j];
            double t=std::clamp(along/squares[j],0.,1.);
            double square=dot(relative,relative)-2*t*along+t*t*squares[j];
            if(square<best[i]) {best[i]=square;nearest[i]=j;fractions[i]=t;}
        }
    }
}
}
