import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
export default {targets:[{engine:web(),app:{url:'http://127.0.0.1:0',command:{executable:'node',args:['fixture.cjs','{port}']}}}]} satisfies E2EConfig;
