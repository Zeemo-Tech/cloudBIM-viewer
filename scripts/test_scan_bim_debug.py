"""HTTP boundary tests for the isolated local workbench, no production writes."""
import importlib.util
import gzip
import json
from pathlib import Path
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

path=Path(__file__).with_name('scan-bim-debug.py')
spec=importlib.util.spec_from_file_location('scan_bim_debug',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Stub:
    catalog={'bars':[]}
    output=Path('/nonexistent')
    calls=0
    def snapshot(self): return {'status':'idle'}
    def runs(self): return []
    def start(self,bar,params): self.calls+=1;return 'accepted-request'

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.state=Stub();self.server=ThreadingHTTPServer(('127.0.0.1',0),module.handler_for(self.state))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,headers=None,body=None):
        request=Request(self.base+path,headers=headers or {},data=body)
        try:
            with urlopen(request) as response:return response.status,response.read()
        except HTTPError as exc:return exc.code,exc.read()
    def test_read_only_local_catalog_and_no_traversal(self):
        self.assertEqual(self.request('/api/catalog')[0],200)
        self.assertEqual(self.request('/vendor/addons/../../../package.json')[0],404)
        self.assertEqual(self.request('/runs/../../.env')[0],404)
        self.assertEqual(self.request('/api/catalog',{'Host':'evil.example'})[0],403)
    def test_origin_and_body_are_checked_before_mutation(self):
        body=json.dumps({'ifcGlobalId':'A','parameters':{}}).encode()
        self.assertEqual(self.request('/api/run',{'Content-Type':'application/json','Origin':'http://evil.example'},body)[0],403)
        self.assertEqual(self.request('/api/run',{'Content-Type':'text/plain'},body)[0],400)
        self.assertEqual(self.state.calls,0)
        status,payload=self.request('/api/run',{'Content-Type':'application/json','Origin':self.base},body)
        self.assertEqual(status,202)
        self.assertEqual(json.loads(payload)['requestId'],'accepted-request')
        self.assertEqual(self.state.calls,1)
    def test_parameter_validation_rejects_bad_numbers(self):
        state=module.Workbench.__new__(module.Workbench)
        state.catalog={'bars':[{'ifcGlobalId':'A'}], 'defaults':{'normalMaxAngleDeg':30,'maxSearchDistanceMm':200,'knnK':32,'maxSamples':64,'windowScale':1,'minArcCoverageDeg':160}}
        for params in [{'knnK':1.5},{'maxSamples':True},{'windowScale':float('nan')},{'windowScale':0},{'unexpected':1}]:
            with self.assertRaises(ValueError):state.start('A',params)

    def test_result_transfer_is_lossless_compressed_and_browser_cacheable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.state.output=Path(directory)
            run=self.state.output/'20260913T120000-12345678';run.mkdir()
            raw=json.dumps({'points':list(range(30000)), 'name':'钢筋'}).encode()
            (run/'result.json').write_bytes(raw)
            url=self.base+'/runs/'+run.name+'/result.json'
            with urlopen(Request(url,headers={'Accept-Encoding':'gzip'})) as response:
                packed=response.read()
                self.assertEqual(response.headers.get('Content-Encoding'),'gzip')
                self.assertIn('immutable',response.headers.get('Cache-Control',''))
                self.assertEqual(response.headers.get('Vary'),'Accept-Encoding')
                self.assertEqual(int(response.headers['Content-Length']),len(packed))
                self.assertIn('application/json',response.headers['Content-Type'])
            self.assertEqual(gzip.decompress(packed),raw)
            self.assertLess(len(packed),len(raw)//2)
            for encoding in ['identity','gzip;q=0, *;q=1']:
                with urlopen(Request(url,headers={'Accept-Encoding':encoding})) as response:
                    self.assertIsNone(response.headers.get('Content-Encoding'))
                    self.assertEqual(response.read(),raw)
            with urlopen(self.base+'/api/catalog') as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store')
            with urlopen(self.base+'/viewer.js') as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store')

class SessionTests(unittest.TestCase):
    def test_current_defaults_remain_available_beyond_recent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            state=module.Workbench.__new__(module.Workbench)
            state.output=Path(directory);state.catalog={'sessionId':'inputs-v1'}
            for index in range(103):
                folder=state.output/str(index);folder.mkdir()
                row={'runId':str(index),'createdAt':f'{index:05d}','sessionId':'inputs-v1',
                     'ifcGlobalId':'A' if index==0 else 'B','parameters':{'windowScale':1}}
                (folder/'summary.json').write_text(json.dumps(row))
            self.assertIn('0',{row['runId'] for row in state.runs()})
            state.catalog['sessionId']='inputs-v2'
            self.assertNotIn('0',{row['runId'] for row in state.runs()})

    def test_results_survive_restart_but_expire_with_changed_inputs(self):
        defaults={'normalMaxAngleDeg':30,'maxSearchDistanceMm':200,'knnK':32,'maxSamples':64,'windowScale':1,'minArcCoverageDeg':160}
        with tempfile.TemporaryDirectory() as directory, \
             patch('rebar_workbench.load_inputs', return_value={}), \
             patch('rebar_workbench.catalog', side_effect=lambda _: {'bars':[{'ifcGlobalId':'A'}], 'defaults':defaults}), \
             patch.object(module, 'input_fingerprint', return_value='inputs-v1') as identity, \
             patch('rebar_workbench.run_bar', return_value={'parameters':defaults,'stats':{}}) as compute:
            output=Path(directory)
            first=module.Workbench(output/'inputs.json',output)
            def run():
                request_id=first.start('A',{})
                self.assertIsInstance(request_id,str)
                deadline=time.monotonic()+5
                while first.snapshot()['status']=='running' and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertEqual(first.snapshot()['status'],'complete')
                record=first.snapshot()['latest']
                result=json.loads((output/record['runId']/'result.json').read_text())
                self.assertEqual(record['sessionId'],first.catalog['sessionId'])
                self.assertEqual(record['requestId'],request_id)
                self.assertEqual(first.snapshot()['requestId'],request_id)
                self.assertEqual(result['sessionId'],record['sessionId'])
                return record
            original=run(); forced=run()
            self.assertNotEqual(original['runId'],forced['runId'])
            self.assertNotEqual(original['requestId'],forced['requestId'])
            self.assertEqual(compute.call_count,2)
            restarted=module.Workbench(output/'inputs.json',output)
            self.assertEqual(first.catalog['sessionId'],restarted.catalog['sessionId'])
            self.assertEqual(len(restarted.runs()),2)
            restarted.prepare_defaults()
            self.assertEqual(compute.call_count,2,'Preparation must skip valid default results')
            identity.return_value='inputs-v2'
            changed=module.Workbench(output/'inputs.json',output)
            self.assertTrue(all(r['sessionId']!=changed.catalog['sessionId'] for r in changed.runs()))
            changed.prepare_defaults()
            self.assertEqual(compute.call_count,3)

    def test_identity_checks_input_and_algorithm_bytes_not_ui_or_timestamps(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module,'ROOT',Path(directory)):
            root=Path(directory);sources=root/'services/mesh-service';sources.mkdir(parents=True)
            (sources/'requirements.txt').write_text('numpy')
            (sources/'rebar_workbench.py').write_text('import axis')
            algorithm=sources/'axis.py';algorithm.write_text('v1')
            mesh=root/'mesh';mesh.mkdir();tile=mesh/'tile.glb';tile.write_bytes(b'mesh')
            scan=root/'scan.las';scan.write_bytes(b'scan')
            mapping=root/'map.json';mapping.write_text('{}')
            loaded={'scan_path':str(scan),'instance_map_path':str(mapping),'analysis_mesh_path':str(mesh),'alignment_matrix':[1,0]}
            original=module.input_fingerprint(loaded)
            scan.touch();(root/'viewer.js').write_text('UI changed')
            (sources/'test_axis.py').write_text('new test')
            self.assertEqual(module.input_fingerprint(loaded),original)
            for file in [scan,mapping,tile,algorithm]:
                previous=file.read_bytes();file.write_bytes(previous+b'changed')
                self.assertNotEqual(module.input_fingerprint(loaded),original)
                file.write_bytes(previous)
            loaded['alignment_matrix']=[0,1]
            self.assertNotEqual(module.input_fingerprint(loaded),original)

    def test_source_identity_tracks_transitive_and_lazy_imports_only(self):
        with tempfile.TemporaryDirectory() as directory:
            sources=Path(directory)
            (sources/'rebar_workbench.py').write_text('from package.core import run\ndef later():\n import lazy_axis\n')
            (sources/'package').mkdir()
            (sources/'package/__init__.py').write_text('from . import startup')
            (sources/'package/core.py').write_text('from .geometry import run')
            (sources/'package/geometry.py').write_text('def run(): pass')
            (sources/'package/startup.py').write_text('')
            (sources/'lazy_axis.py').write_text('')
            unrelated=sources/'denoise.py';unrelated.write_text('v1')
            expected={'rebar_workbench.py','package/__init__.py','package/core.py','package/geometry.py','package/startup.py','lazy_axis.py'}
            self.assertEqual({str(p.relative_to(sources)) for p in module.numerical_sources(sources)},expected)
            unrelated.write_text('v2')
            self.assertEqual({str(p.relative_to(sources)) for p in module.numerical_sources(sources)},expected)
            (sources/'lazy_axis.py').write_text('import denoise')
            self.assertIn(unrelated,module.numerical_sources(sources))
            (sources/'lazy_axis.py').write_text('import importlib\nimportlib.import_module("denoise")')
            self.assertIn(unrelated,module.numerical_sources(sources),'Dynamic imports must conservatively retain all numerical sources')

    def test_fixed_scan_comparison_does_not_depend_on_denoise_registry(self):
        sources=module.ROOT/'services/mesh-service'
        files={str(p.relative_to(sources)) for p in module.numerical_sources(sources)}
        self.assertTrue({'rebar_workbench.py','rebar_scan_surface.py','rebar_prior_axis.py','rebar_cluster_axis.py',
                         'analysis_c2m/core.py','algorithms/c2m_distance.py','algorithms/__init__.py'} <= files)
        self.assertNotIn('algorithms/rebar_geometric_v6.py',files)
        self.assertNotIn('algorithms/rebar_control_net.py',files)

if __name__=='__main__':unittest.main()
