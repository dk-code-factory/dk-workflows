import contextlib, importlib.util, io, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest.mock as mock
SOURCE=pathlib.Path(__file__).resolve().parents[1]
class Fixture:
 def __init__(self, base, case, mode='notify', host='codex'):
  self.base=base/case; self.base.mkdir(); self.host=host; self.calls=[]; self.network=[]; self.versions={}; self.records=[]
  self.env={'XDG_CONFIG_HOME':str(self.base/'config'),'XDG_STATE_HOME':str(self.base/'state'),'CLAUDE_CONFIG_DIR':str(self.base/'claude')}
  self.modules={}
  for name in ['dk-workflows','code-review-qodo']:
   root=self.base/'installed'/name; (root/'scripts').mkdir(parents=True)
   shutil.copy2(SOURCE/'scripts/update.py',root/'scripts/update.py')
   for h in ['codex','claude']:
    (root/f'.{h}-plugin').mkdir(); (root/f'.{h}-plugin/plugin.json').write_text(json.dumps({'name':name,'version':'1.0.0'}))
   spec=importlib.util.spec_from_file_location(case+name.replace('-','_'),root/'scripts/update.py'); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);self.modules[name]=mod
   self.versions[name]='1.0.0';self.records.append({'name':name,'pluginId':name+'@factory','version':'1.0.0','scope':'user','source':{'source':'github','ref':'main'},'installPath':str(root)})
  cfg=self.base/'config/dk-code-factory/updates.json';cfg.parent.mkdir(parents=True);cfg.write_text(json.dumps({'mode':mode}))
  self.write_claude()
 def write_claude(self):
  p=self.base/'claude/plugins';p.mkdir(parents=True,exist_ok=True)
  (p/'installed_plugins.json').write_text(json.dumps({'plugins':{r['pluginId']:[r] for r in self.records}}))
  (p/'known_marketplaces.json').write_text(json.dumps({'factory':{'source':{'source':'github','ref':'main'}}}))
 def subrun(self,argv,**kw):
  self.calls.append(argv)
  if argv[0]=='git':return subprocess.CompletedProcess(argv,1,'','not a repo')
  if argv[0]=='codex' and argv[1:]==['plugin','list','--json']:
   return subprocess.CompletedProcess(argv,0,json.dumps({'installed':self.records}),'')
  if argv[:3] in [['codex','plugin','add'],['claude','plugin','update']]:
   name=argv[3].split('@')[0]
   for r in self.records:
    if r['name']==name:r['version']='1.1.0'
   self.write_claude()
  return subprocess.CompletedProcess(argv,0,'','')
 def urlopen(self,request,**kwargs):
  self.network.append(request.full_url)
  return io.StringIO(json.dumps({'tag_name':'v1.1.0','draft':False,'prerelease':False}))
 def invoke(self,name='dk-workflows',phase='entry',**kwargs):
  mod=self.modules[name]
  argv=['update.py',phase,'--host',self.host]
  for k,v in kwargs.items():argv+=['--'+k,str(v)]
  out=io.StringIO()
  with mock.patch.dict(os.environ,self.env,clear=False),mock.patch.object(sys,'argv',argv),mock.patch.object(mod.subprocess,'run',side_effect=self.subrun),mock.patch.object(mod.urllib.request,'urlopen',side_effect=self.urlopen),contextlib.redirect_stdout(out):
   mod.main()
  return json.loads(out.getvalue())
 def state(self):return json.loads((self.base/'state/dk-code-factory/updates/state.json').read_text())
 def print(self,**kwargs):print(json.dumps(kwargs,indent=2))
