import {tokens} from '../tokens.js';

const opticalRecords = new WeakMap();
export const opticClamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));

export function opticRange(value, min, max, step = 1) {
  if (![value, min, max, step].every(Number.isFinite) || max <= min || step <= 0) throw new TypeError('Invalid optical control range');
  return Number(opticClamp(min + Math.round((value - min) / step) * step, min, max).toFixed(8));
}

export function opticXY(value, min, max, step) {
  return {x: opticRange(value.x, min, max, step), y: opticRange(value.y, min, max, step)};
}

export function opticPoint(clientX, clientY, rect, inset = 0) {
  return {x: opticClamp((clientX - rect.left - inset) / Math.max(1, rect.width - inset * 2)), y: 1 - opticClamp((clientY - rect.top - inset) / Math.max(1, rect.height - inset * 2))};
}

/** Attraction follows the nearest enabled centre, even between the wells. */
export function opticDropTarget(point, targets) {
  let nearest=null,distance=Infinity;
  for(const target of targets){if(target.disabled)continue;const next=(point.x-target.x)**2+(point.y-target.y)**2;if(next<distance){nearest=target.value;distance=next;}}
  return nearest;
}

export function opticColor(name = 'green') {
  if (!['green', 'gold'].includes(name)) throw new TypeError('Completion color must be green or gold');
  const hex = tokens.optics[name];
  return [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
}

/** Stateless sampling keeps interruption, hidden tabs and reduced motion deterministic. */
export function sampleOpticalTransition(record, now, reduced = false) {
  const duration = record.duration || 0;
  const position = reduced || !duration ? 1 : opticClamp((now - record.start) / duration);
  const eased = 1 - (1 - position) ** 3;
  const result = {...record.to};
  for (const key of ['fill', 'complete', 'selected', 'hover']) {
    const end = record.to[key] || 0, start = record.from[key] || 0;
    const t = key === 'complete' && end > start ? opticClamp((position - .12) / .88) : eased;
    result[key] = t === 1 ? end : start + (end - start) * t;
  }
  if (typeof record.to.opacity === 'number') result.opacity = position === 1 ? record.to.opacity : (record.from.opacity ?? record.to.opacity) + (record.to.opacity - (record.from.opacity ?? record.to.opacity)) * eased;
  const elapsed = now - record.flowStart;
  result.flow = !reduced && record.flowStart !== null && elapsed >= 0 && elapsed < tokens.optics.flowDuration ? elapsed / tokens.optics.flowDuration : -1;
  return result;
}

export function readOpticalState(element, now = performance.now(), reduced = false) {
  const record = opticalRecords.get(element);
  return record ? sampleOpticalTransition(record, now, reduced && !record.to.forceMotion) : null;
}

export function setOpticalState(element, patch, {animate = true, pulse = false, duration: requestedDuration} = {}) {
  const now = performance.now(), prior = opticalRecords.get(element);
  const reduced = !(patch.forceMotion ?? prior?.to.forceMotion) && typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const from = prior ? sampleOpticalTransition(prior, now, reduced) : {fill: 0, complete: 0, selected: 0, hover: 0};
  const to = {...(prior?.to || from), ...patch};
  const duration = !animate || reduced ? 0 : requestedDuration ?? (pulse ? tokens.optics.flowDuration : tokens.optics.settleDuration);
  const sameSelection = to.selected === prior?.to.selected && to.complete === prior?.to.complete;
  const record = {from, to, start: now, duration, flowStart: animate && !reduced ? pulse ? now : sameSelection ? prior?.flowStart ?? null : null : null};
  opticalRecords.set(element, record);
  element.dispatchEvent(new CustomEvent('lg:optics', {bubbles: true, detail: {duration:Math.max(duration,animate&&!reduced&&pulse?tokens.optics.flowDuration:0)}}));
  return record;
}

export function clearOpticalState(element) { opticalRecords.delete(element); }

export function opticalSelection(element, selected, color = 'green', animate = true) {
  const wasSelected = (opticalRecords.get(element)?.to.selected || 0) > .5;
  element.style.setProperty('--lg-optical-color', tokens.optics[color] || tokens.optics.green);
  setOpticalState(element, {selected: selected ? 1 : 0, color: opticColor(color)}, {animate, pulse: selected && !wasSelected});
  const beam = element.querySelector('.lg-optic-beam');
  if (beam) {
    beam.getAnimations().forEach(animation => animation.cancel());
    if (selected && !wasSelected && animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) beam.animate([{transform: 'rotate(-100deg)', opacity: 0}, {opacity: 1, offset: .12}, {transform: 'rotate(260deg)', opacity: 0}], {duration: tokens.optics.flowDuration, easing: 'ease-out'});
  }
}
