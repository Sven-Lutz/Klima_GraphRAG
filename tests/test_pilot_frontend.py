"""JavaScript logic fixtures, explicitly not browser or accessibility acceptance."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_feedback_defaults_opt_in_blank_validation_and_no_automatic_transfer(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; no browser result inferred")
    source = Path(__file__).resolve().parents[1] / "crawler/web/static/feedback.js"
    script = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements={};
for(const id of ['feedback-kind','feedback-context','feedback-message','feedback-json',
'feedback-preview','feedback-form','feedback-status','feedback-save',
'feedback-history','feedback-stored']) {
  elements[id]={value:'',checked:false,textContent:'',focus(){this.focused=true}};
}
elements['feedback-kind'].value='fact';elements['feedback-message'].value='TECHNICAL TEST ONLY';
let exported=null,clicks=0;
const context={
 document:{getElementById:id=>elements[id],createElement:()=>({click(){clicks++}})},
 window:{pilotFeedbackContext:()=>({software:'preview-sha256:fixture',snapshot:'snapshot:fixture',
 projection:'graph:fixture',question:'PRIVATE_TECHNICAL_QUERY',evidence_ids:['e1']})},
 URL:{createObjectURL:b=>{exported=b;return 'blob:fixture'},revokeObjectURL(){}},
 Blob,setTimeout:fn=>fn()
};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),context);
elements['feedback-preview'].onclick();let data=JSON.parse(elements['feedback-json'].textContent);
assert.equal(data.context,null);assert(!elements['feedback-json'].textContent.includes('PRIVATE_TECHNICAL_QUERY'));assert.equal(clicks,0);
elements['feedback-context'].checked=true;elements['feedback-preview'].onclick();data=JSON.parse(elements['feedback-json'].textContent);assert.equal(data.context.question,'PRIVATE_TECHNICAL_QUERY');
elements['feedback-form'].onsubmit({preventDefault(){}});assert.equal(clicks,1);assert.equal(data.research_gold,false);assert.equal(data.transfer,'MANUAL_ONLY');assert(exported);
elements['feedback-message'].value='   ';
elements['feedback-form'].onsubmit({preventDefault(){}});
assert.equal(clicks,1);assert(elements['feedback-message'].focused);
console.log('feedback VM fixtures passed; no browser claims');
"""
    driver = tmp_path / "feedback.cjs"
    driver.write_text(script)
    result = subprocess.run(
        [node, str(driver), str(source)], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
