"""Graph state-machine fixtures; these do not establish browser/UX acceptance."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_graph_selection_invalidates_pending_evidence_and_exports(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; no browser result inferred")
    source = Path(__file__).resolve().parents[1] / "crawler/web/static/graph.js"
    script = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
class Element {
  constructor(){this.children=[];this.value='';this.dataset={};this.hidden=false;this.clientWidth=1440;}
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=children}
  removeAttribute(name){delete this[name]}
  focus(){} scrollIntoView(){} addEventListener(){}
}
const elements={};
const el=id=>elements[id]??(elements[id]=new Element());
const graph={nodes:[{id:'a',label:'Entity A',type:'Project',distance:0,
identity_basis:'EXACT_TECHNICAL_BASIS'},
{id:'b',label:'Entity B',type:'Project',distance:1}],
edges:[{id:'ab',source:'a',target:'b',predicate:'supports'}],chain:[],subgraph_sha256:'fixture'};
const overview={entity_index:graph.nodes,default_entity:'a',entities:2,relations:1,
source_versions:1,relation_types:{supports:1},entity_types:{Project:2},reference_years:[]};
let pendingEdge, pendingGraph, deferGraph=false, inspected=0, downloads=0;
const response=body=>({ok:true,json:async()=>body});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const context={
  document:{getElementById:el,createElement:()=>new Element(),documentElement:{}},window:{},
  Option:class extends Element {
    constructor(label,value){super();this.textContent=label;this.value=value}
  },
  fetch:url=>url==='/api/graph'?Promise.resolve(response(overview)):
    url.startsWith('/api/graph?')?(deferGraph?new Promise(resolve=>pendingGraph=resolve):
      Promise.resolve(response(graph))):
    new Promise(resolve=>pendingEdge=resolve),
  getComputedStyle:()=>({getPropertyValue:()=>''}),
  cytoscape:()=>({on(){},destroy(){},edges:()=>({unselect(){}}),nodes:()=>({unselect(){},positions(){}}),
    getElementById:()=>({select(){},addClass(){}})}),
  ResizeObserver:class {observe(){}},URLSearchParams,
  inspect(){inspected++;el('inspector').replaceChildren(new Element())},
  URL:{createObjectURL(){downloads++;return 'blob:fixture'},revokeObjectURL(){}},Blob,setTimeout
};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),context);
(async()=>{
 await context.window.openGraphExplorer();
 assert.equal(el('graph-export').disabled,false);
 let list=el('graph-accessible');
 list.children[2].onclick(); // request edge evidence
 const stale=pendingEdge;
 list.children[0].onclick(); // switch to node before evidence returns
 assert.equal(el('graph-evidence-export').hidden,true);
 stale(response({result_id:'stale',evidence:[]}));await flush();
 assert.equal(inspected,0);
 assert.equal(el('inspector').children[0].textContent,'Entity A');
 assert.equal(el('inspector').children[1].textContent,'Identität vorläufig');
 const identity=el('inspector').children[3];
 assert.equal(identity.children[0].textContent,'Identitätsgrundlage');
 assert.equal(identity.children[1].textContent,'Project');
 assert.equal(identity.children[2].textContent,'EXACT_TECHNICAL_BASIS');
 list.children[2].onclick();
 pendingEdge(response({result_id:'current',evidence:[{evidence_id:'e',assertions:[{assertion_id:'ab'}]}]}));
 await flush();
 assert.equal(inspected,1);assert.equal(el('graph-evidence-export').hidden,false);
 list.children[1].onclick();
 assert.equal(el('graph-evidence-export').hidden,true);
 assert.equal(el('graph-evidence-export').href,undefined);
 // An in-flight graph navigation clears all old interactions immediately.
 deferGraph=true;
 const loading=context.window.openGraphExplorer();
 assert.equal(el('graph-export').disabled,true);
 assert.equal(el('graph-accessible').children.length,0);
 assert.equal(context.window.klimaGraph,undefined);
 el('graph-export').onclick();assert.equal(downloads,0);
 assert.equal(el('graph-evidence-export').href,undefined);
 pendingGraph(response(graph));await loading;
 assert.equal(el('graph-export').disabled,false);
 console.log('graph VM race fixtures passed; no browser claims');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    driver = tmp_path / "graph.cjs"
    driver.write_text(script)
    result = subprocess.run(
        [node, str(driver), str(source)], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
