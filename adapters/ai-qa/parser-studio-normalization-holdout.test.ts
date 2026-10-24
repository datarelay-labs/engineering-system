/** Requirements-derived independent QA: hierarchical output destinations cannot overlap. */
import {expect,it} from 'vitest';
import {validateNormalizationMappings} from './normalizationValidation';
import type {SuiteResult} from '../model';

it('rejects overlapping nested destinations, not only identical mapping targets',()=>{
  const suite={events:[{fields:{username:'alice',user_id:'42'}}]} as unknown as SuiteResult;
  const actual=validateNormalizationMappings(suite,[
    {id:'parent',source:'username',target:'custom.actor',type:'string'},
    {id:'child',source:'user_id',target:'custom.actor.id',type:'string'},
  ]);
  expect(actual.filter(f=>f.code==='TARGET_COLLISION')).toHaveLength(2);
});
