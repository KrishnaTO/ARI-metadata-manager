// Pathophysiology deep-dive: the interactive force-directed pathograph graph
// (D3) plus its colour maps and step-detail rendering.

// Knowledge-graph palette (AurInt design system).
const CAT_COLORS = { genetic:'#6C57A3', trigger:'#B5841A', immune:'#3F6699', antibody:'#2F8F6F', tissuedamage:'#B7382D', outcome:'#1F6B7A' };
const MED_COLORS = { gene:'#6C57A3', env:'#B5841A', antigen:'#3F6699', apc:'#B5841A', antibody:'#2F8F6F', tcell:'#B7382D', cyto:'#6C57A3', complement:'#1F6B7A', inflammasome:'#B7382D', netosis:'#6C57A3' };
function nodeColor(n){ return n.kind==='step' ? (CAT_COLORS[n.cat]||'#5C6675') : (MED_COLORS[n.type]||'#8A93A2'); }
function trunc(s, n){ s = String(s); return s.length > n ? s.slice(0, n-1)+'…' : s; }

function pathwayLegend(){
  const items = [['Genetic','#6C57A3'],['Trigger','#B5841A'],['Immune','#3F6699'],['Antibody','#2F8F6F'],['Tissue damage','#B7382D'],['Outcome','#1F6B7A']];
  return `<div class="graph-legend">` + items.map(([l,c]) => `<span><i style="background:${c}"></i>${l}</span>`).join('') + `</div>`;
}

function showStepDetail(p, d){
  let det = `<div class="card"><h3>${esc(p.order)}. ${esc(p.name)} <span class="cat-pill">${esc(p.category)}</span></h3>`+
    `<div class="desc">${esc(p.description)}</div>`+
    `<div class="src">${(p.source||[]).map(s => `<a href="${esc(s)}" target="_blank">${esc(s)}</a>`).join('<br>')}</div></div>`;
  if ((p.category||'').toLowerCase() === 'antibody' && d.antibodies?.length){
    det += `<div class="card"><h3>Autoantibodies at this step</h3><div class="desc">`+
      d.antibodies.map(a => `<div><span class="ab-highlight">${esc(a.name)}</span> &mdash; ${esc(first(a.frequency))}</div>`).join('')+
      `</div></div>`;
  }
  $('#step-detail').innerHTML = det;
}

function renderPathophysiology(d, panel){
  let html = closeHeader('Pathophysiology map');
  html += `<p style="font-size:12px;color:var(--muted);margin:0 0 6px">Interactive pathograph — numbered nodes are the mechanistic cascade; surrounding nodes are the associated genetic, antigen, <span class="ab-highlight">antibody</span> and immune mediators. Drag nodes to explore; click a step for sources.</p>`;
  html += pathwayLegend();
  html += `<div id="patho-graph" class="patho-graph"></div>`;
  html += `<div id="step-detail"></div>`;

  if (d.genetic?.length){
    html += `<div class="section-label">Genetic associations in this pathway</div>`;
    html += `<table class="data-table"><thead><tr><th>Gene / HLA</th><th>Effect</th><th>OR</th></tr></thead><tbody>`;
    for (const g of d.genetic.slice(0, 8)){
      html += `<tr><td><strong>${esc(g.name)}</strong></td><td>${esc(first(g.risk_effect)||first(g.hla_effect))}</td><td>${esc(first(g.odds_ratio))}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  panel.innerHTML = html;
  drawPathwayGraph(d, panel);
}

function drawPathwayGraph(d, panel){
  const container = panel.querySelector('#patho-graph');
  if (!container || !d.pathway?.length) return;
  const steps = [...d.pathway].sort((a,b)=>a.order-b.order);
  const width = container.clientWidth || 460;
  const stepGap = 62;
  const height = Math.max(420, steps.length * stepGap + 60);
  const cx = width * 0.42;

  const nodes = [], links = [], byOrder = {};
  steps.forEach((p, i) => {
    const n = { id:'step_'+i, kind:'step', label:p.name, cat:(p.category||'').toLowerCase(), order:p.order, data:p, fx:cx, fy:40 + i*stepGap };
    nodes.push(n); byOrder[p.order] = n;
    if (i > 0) links.push({ source:'step_'+(i-1), target:'step_'+i, spine:true });
  });
  const attach = (order, arr, type, max) => {
    const step = byOrder[order];
    if (!step || !arr) return;
    arr.slice(0, max).forEach((m, k) => {
      const id = type+'_'+order+'_'+k;
      nodes.push({ id, kind:'med', type, label:m.name, x: cx + (k%2 ? 90 : -90), y: step.fy + (k-1)*8 });
      links.push({ source:id, target:step.id });
    });
  };
  attach(1, d.genetic, 'gene', 4);
  attach(2, d.environmental_factors, 'env', 3);
  attach(4, d.antigens, 'antigen', 4);
  attach(4, d.apcs, 'apc', 2);
  attach(5, d.antibodies, 'antibody', 5);
  attach(6, d.tcells, 'tcell', 3);
  attach(6, d.cytokines, 'cyto', 3);
  attach(7, d.complement, 'complement', 2);
  attach(7, d.inflammasome, 'inflammasome', 2);
  attach(7, d.netosis, 'netosis', 2);

  const svg = d3.select(container).append('svg')
    .attr('width', '100%').attr('viewBox', `0 0 ${width} ${height}`).style('height', height+'px');

  const link = svg.append('g').selectAll('line').data(links).enter().append('line')
    .attr('stroke', l => l.spine ? '#94a3b8' : '#cbd5e1')
    .attr('stroke-width', l => l.spine ? 2.5 : 1);

  const node = svg.append('g').selectAll('g').data(nodes).enter().append('g')
    .style('cursor', n => n.kind === 'step' ? 'pointer' : 'grab')
    .call(d3.drag().on('start', dragstart).on('drag', dragged).on('end', dragend));

  node.append('circle')
    .attr('r', n => n.kind === 'step' ? 15 : 6)
    .attr('fill', nodeColor)
    .attr('stroke', n => (n.kind==='step' && n.cat==='antibody') || n.type==='antibody' ? '#059669' : '#fff')
    .attr('stroke-width', n => (n.kind==='step' && n.cat==='antibody') || n.type==='antibody' ? 2.5 : 1.5);
  node.filter(n => n.kind === 'step').append('text')
    .text(n => n.order).attr('text-anchor','middle').attr('dy','0.35em')
    .attr('fill','#fff').style('font-size','11px').style('font-weight','700').style('pointer-events','none');
  node.append('text')
    .text(n => n.kind === 'step' ? n.label : trunc(n.label, 24))
    .attr('x', n => n.kind === 'step' ? 20 : 9).attr('dy','0.35em')
    .style('font-size', n => n.kind === 'step' ? '12px' : '10px')
    .style('font-weight', n => n.kind === 'step' ? '600' : '400')
    .style('fill', n => n.type === 'antibody' ? '#065f46' : '#0f172a')
    .style('pointer-events','none');
  node.append('title').text(n => n.label);
  node.filter(n => n.kind === 'step').on('click', (ev, n) => showStepDetail(n.data, d));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(x => x.id).distance(l => l.spine ? stepGap : 48).strength(l => l.spine ? 1 : 0.5))
    .force('charge', d3.forceManyBody().strength(-130))
    .force('collide', d3.forceCollide().radius(n => n.kind === 'step' ? 66 : 30))
    .force('x', d3.forceX(width/2).strength(0.02))
    .on('tick', () => {
      nodes.forEach(n => { if (n.kind === 'med'){ n.x = Math.max(60, Math.min(width-60, n.x)); n.y = Math.max(20, Math.min(height-20, n.y)); }});
      link.attr('x1', l => l.source.x).attr('y1', l => l.source.y).attr('x2', l => l.target.x).attr('y2', l => l.target.y);
      node.attr('transform', n => `translate(${n.x},${n.y})`);
    });
  function dragstart(ev, n){ if (!ev.active) sim.alphaTarget(0.3).restart(); n.fx = n.x; n.fy = n.y; }
  function dragged(ev, n){ n.fx = ev.x; n.fy = ev.y; }
  function dragend(ev, n){ if (!ev.active) sim.alphaTarget(0); if (n.kind === 'med'){ n.fx = null; n.fy = null; } }
}
