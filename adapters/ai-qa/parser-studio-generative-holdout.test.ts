// Independent property holdout: not authored or installed in the product repository.
// The CI driver copies this file into an immutable source snapshot's disposable clone.
import {expect,it} from 'vitest';
import fc from 'fast-check';
import {validateNormalizationMappings} from './normalizationValidation';
import type {SuiteResult} from '../model';

const SEED=20261122;
const OPTIONS={seed:SEED,numRuns:120,verbose:true} as const;
const segment=fc.constantFrom('alpha','beta','gamma','delta','sensor','actor');
const value=fc.constantFrom('plain','café','한국어','日本語','a=b,c','198.51.100.7','zero');

function event(fields:Record<string,unknown>):SuiteResult{
  return {events:[{fields}]} as unknown as SuiteResult;
}
function rule(id:string,source:string,target:string){
  return {id,source,target,type:'string' as const};
}
const vendorGroups={one:'vendor_fields',two:'vendor_fields',three:'vendor_fields'} as const;

it('property: nested mapping targets collide symmetrically without losing source values',()=>{
  fc.assert(fc.property(segment,segment,value,value,(parent,child,a,b)=>{
    const fields={left:a,right:b};
    const before=JSON.stringify(fields);
    const mappings=[rule('one','left',`custom.${parent}`),
                    rule('two','right',`custom.${parent}.${child}`)];
    const actual=validateNormalizationMappings(event(fields),mappings,vendorGroups);
    const collisions=actual.filter(row=>row.code==='TARGET_COLLISION');
    expect(collisions).toHaveLength(2);
    expect(new Set(collisions.map(row=>row.source))).toEqual(new Set(['left','right']));
    expect(actual.some(row=>row.code==='SOURCE_NOT_FOUND')).toBe(false);
    expect(JSON.stringify(fields)).toBe(before);
  }),OPTIONS);
});

it('property: a shared string prefix without the dot boundary is not a collision',()=>{
  fc.assert(fc.property(segment,value,value,(parent,a,b)=>{
    const actual=validateNormalizationMappings(event({left:a,right:b}),[
      rule('one','left',`custom.${parent}`),
      rule('two','right',`custom.${parent}suffix`),
    ],vendorGroups);
    expect(actual.filter(row=>row.code==='TARGET_COLLISION')).toEqual([]);
    expect(actual.some(row=>row.code==='SOURCE_NOT_FOUND')).toBe(false);
  }),OPTIONS);
});

it('property: missing source is reported per event and never fabricated',()=>{
  fc.assert(fc.property(segment,value,(suffix,a)=>{
    const fields={present:a};
    const actual=validateNormalizationMappings(event(fields),[
      rule('one','present',`custom.first.${suffix}`),
      rule('two','missing',`custom.second.${suffix}`),
    ],vendorGroups);
    expect(actual.filter(row=>row.code==='SOURCE_NOT_FOUND'))
      .toEqual([expect.objectContaining({source:'missing',event:1})]);
    expect(actual.some(row=>row.code==='TYPE_ERROR'&&row.source==='present')).toBe(false);
    expect(fields.present).toBe(a);
  }),OPTIONS);
});

it('property: mapping order does not change the collision and absence findings',()=>{
  fc.assert(fc.property(segment,segment,value,(parent,child,a)=>{
    const mappings=[rule('one','left',`custom.${parent}`),
                    rule('two','right',`custom.${parent}.${child}`),
                    rule('three','missing','custom.unrelated')];
    const fields={left:a,right:a};
    const describe=(rows:ReturnType<typeof validateNormalizationMappings>)=>
      rows.map(row=>`${row.code}:${row.source}:${row.target}:${row.event}`).sort();
    const forward=validateNormalizationMappings(event(fields),mappings,vendorGroups);
    const reversed=validateNormalizationMappings(event(fields),[...mappings].reverse(),vendorGroups);
    expect(describe(reversed)).toEqual(describe(forward));
  }),OPTIONS);
});
