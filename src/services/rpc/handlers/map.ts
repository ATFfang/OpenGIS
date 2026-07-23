/**
 * rpc.ui.map.* handlers
 *
 * Thin registry facade. Concrete behavior lives in ./map domain modules so
 * layer IO, raster IO, camera/view changes, dynamic workers, styling, and
 * export can evolve independently without turning this file back into a hub.
 */

import type { RpcHandler } from '../registry';
import { dynamicHandlers } from './map/dynamic';
import { exportHandlers } from './map/export';
import { layerHandlers } from './map/layers';
import { rasterHandlers } from './map/raster';
import { styleHandlers } from './map/style';
import { viewHandlers } from './map/view';
import { tiles3dHandlers } from './map/tiles3d';

export const mapHandlers: Record<string, RpcHandler> = {
  ...layerHandlers,
  ...rasterHandlers,
  ...styleHandlers,
  ...exportHandlers,
  ...viewHandlers,
  ...dynamicHandlers,
  ...tiles3dHandlers,
};
