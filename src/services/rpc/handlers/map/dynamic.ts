import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { parseParams } from '../_util';
import { bboxToTuple, computeBBox, detectGeometryType, normalizeToFeatureCollection } from '../_map_util';
import { useAssetStore } from '@/stores/assetStore';
import { useMapStore } from '@/stores/mapStore';
import { getDefaultStyle, makeHandledVectorData, resolveVectorGeoJSON, shouldHandleLayer, type GeoJSONFeature, type GeoJSONFeatureCollection, type GeoJSONSourceDiff, type MapLayerDefinition, type ParsedVectorData } from '@/services/geo';
import { extractFields } from '@/services/geo/parsers';
import { DynamicLayerUpdateSchema } from '../schemas';
import { applyPaintToLayerStyle, estimateGeoJSONBytes, normalizePath, renderTypeFromStyleType } from './shared';

export const dynamicHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.dynamic_layer_update': (params) => {
    const parsed = parseParams(
      DynamicLayerUpdateSchema,
      params,
      'rpc.ui.map.dynamic_layer_update',
    );

    const currentWorkspace = useAssetStore.getState().workspacePath;
    if (
      parsed.workspace_path
      && (!currentWorkspace || normalizePath(parsed.workspace_path) !== normalizePath(currentWorkspace))
    ) {
      return {
        layer_id: parsed.layer_id,
        skipped: true,
        reason: 'workspace_mismatch',
      };
    }

    const store = useMapStore.getState();
    const existing = store.getLayerById(parsed.layer_id);
    const existingVector = existing?.data.kind === 'vector' ? existing.data : undefined;
    const mode = parsed.mode ?? (parsed.diff ? 'diff' : 'full');
    const diffResult = mode === 'diff'
      ? applyDynamicLayerDiff(
        existingVector ? resolveVectorGeoJSON(existingVector) : emptyFeatureCollection(),
        parsed.diff,
      )
      : null;
    const normalizedDiff = diffResult?.runtimeDiff;
    const rawFeatureCollection = mode === 'diff'
      ? diffResult?.featureCollection
      : normalizeToFeatureCollection(parsed.geojson);
    const fc = rawFeatureCollection ? normalizeFeatureCollectionIdentities(rawFeatureCollection) : null;
    if (!fc) {
      throw RpcError.invalidParams(
        mode === 'diff'
          ? 'dynamic_layer_update: diff mode requires a valid diff object'
          : 'dynamic_layer_update: geojson is not a FeatureCollection / Feature / bare geometry',
        { method: 'rpc.ui.map.dynamic_layer_update', layer_id: parsed.layer_id },
      );
    }

    const previousSequence = existing?.meta.dynamic?.sequence;
    const previousWorkerStartedAt = existing?.meta.dynamic?.workerStartedAt;
    const sameWorkerRun = parsed.worker_started_at == null
      || previousWorkerStartedAt == null
      || parsed.worker_started_at === previousWorkerStartedAt;
    if (
      typeof parsed.sequence === 'number'
      && typeof previousSequence === 'number'
      && sameWorkerRun
      && parsed.sequence <= previousSequence
    ) {
      return {
        layer_id: parsed.layer_id,
        skipped: true,
        reason: 'stale_sequence',
        sequence: parsed.sequence,
      };
    }

    const schemaChanged = parsed.schema_changed ?? !existingVector;
    const geometryType = schemaChanged || !existingVector
      ? detectGeometryType(fc)
      : existingVector.geometryType;
    const bbox = parsed.bbox
      ? { minX: parsed.bbox[0], minY: parsed.bbox[1], maxX: parsed.bbox[2], maxY: parsed.bbox[3] }
      : computeBBox(fc);
    const displayName = parsed.name ?? existing?.name ?? parsed.layer_id;
    const inlineSize = parsed.size_bytes
      ?? (schemaChanged ? estimateGeoJSONBytes(fc) : existing?.meta.fileSize ?? 0);
    const updateable = mode === 'diff' && isUpdateableFeatureCollection(fc);
    const previousSourceUpdateable = mode === 'diff' && existingVector
      ? hasTopLevelUpdateIds(resolveVectorGeoJSON(existingVector))
      : false;
    const runtimeDiff = mode === 'diff' && updateable && previousSourceUpdateable
      ? normalizedDiff
      : undefined;
    let data: ParsedVectorData = {
      kind: 'vector',
      geojson: fc,
      geometryType,
      featureCount: fc.features.length,
      bbox,
      crs: 'EPSG:4326',
      fields: schemaChanged || !existingVector ? extractFields(fc) : existingVector.fields,
      runtimeDiff,
      runtimeDiffUpdateable: Boolean(runtimeDiff),
    };
    if (shouldHandleLayer(inlineSize)) {
      data = makeHandledVectorData(data, {
        handleId: `vector:${parsed.layer_id}:dynamic:${Date.now()}`,
        sizeBytes: inlineSize,
      });
    }

    const style = parsed.style
      ? getDefaultStyle(geometryType)
      : existing?.style
        ? { ...existing.style }
        : getDefaultStyle(geometryType);
    if (parsed.style) {
      style.renderType = renderTypeFromStyleType(parsed.style.type, geometryType);
      applyPaintToLayerStyle(style, parsed.style.paint);
    }

    const definition: MapLayerDefinition = {
      id: parsed.layer_id,
      name: displayName,
      sourceType: 'geojson',
      visible: parsed.visible ?? existing?.visible ?? true,
      style,
      data,
      meta: {
        fileName: `${displayName}.geojson`,
        extension: '.geojson',
        sourceType: 'geojson',
        fileSize: inlineSize,
        dynamic: {
          workerId: parsed.worker_id,
          workerName: parsed.worker_name,
          workerStartedAt: parsed.worker_started_at,
          sequence: parsed.sequence,
          updatedAt: Date.now(),
          schemaChanged,
          mode,
          updateable,
        },
      },
      addedAt: existing?.addedAt ?? Date.now(),
    };

    store.addLayer(definition);

    return {
      layer_id: parsed.layer_id,
      bbox: bboxToTuple(bbox),
      feature_count: fc.features.length,
      geometry_type: geometryType,
      sequence: parsed.sequence,
      mode,
      updateable,
    };
  }
};


function emptyFeatureCollection(): GeoJSONFeatureCollection {
  return { type: 'FeatureCollection', features: [] };
}

function applyDynamicLayerDiff(
  current: GeoJSONFeatureCollection,
  diff: unknown,
): { featureCollection: GeoJSONFeatureCollection; runtimeDiff: GeoJSONSourceDiff } | null {
  if (!diff || typeof diff !== 'object') return null;
  const raw = diff as Record<string, unknown>;
  const byId = new Map<string, GeoJSONFeature>();
  const runtimeDiff: GeoJSONSourceDiff = {};

  if (!raw.removeAll) {
    for (const feature of current.features) {
      const normalized = normalizeFeatureIdentity(feature);
      const id = featureIdentityKey(normalized);
      if (id !== null) byId.set(id, normalized);
    }
  }

  if (typeof raw.removeAll === 'boolean') runtimeDiff.removeAll = raw.removeAll;
  if (raw.removeAll) byId.clear();

  if (Array.isArray(raw.remove)) {
    const remove = raw.remove.filter(isFeatureId);
    if (remove.length > 0) runtimeDiff.remove = remove;
    for (const id of remove) byId.delete(featureIdKey(id) ?? '');
  }

  if (Array.isArray(raw.add)) {
    const add: GeoJSONFeature[] = [];
    for (const item of raw.add) {
      if (!isGeoJSONFeature(item)) continue;
      const feature = normalizeFeatureIdentity(item);
      const id = featureIdentityKey(feature);
      if (id === null) continue;
      byId.set(id, feature);
      add.push(feature);
    }
    if (add.length > 0) runtimeDiff.add = add;
  }

  if (Array.isArray(raw.update)) {
    const add: GeoJSONFeature[] = runtimeDiff.add ? [...runtimeDiff.add] : [];
    const update: NonNullable<GeoJSONSourceDiff['update']> = [];
    for (const item of raw.update) {
      if (isGeoJSONFeature(item)) {
        const feature = normalizeFeatureIdentity(item);
        const id = featureIdentityKey(feature);
        if (id === null) continue;
        if (byId.has(id)) {
          byId.set(id, feature);
          update.push(featureToReplacementPatch(feature));
        } else {
          byId.set(id, feature);
          add.push(feature);
        }
        continue;
      }

      const patch = normalizeFeaturePatch(item);
      if (!patch) continue;
      const id = featureIdKey(patch.id);
      if (id === null) continue;
      const existing = byId.get(id);
      if (!existing) continue;
      const next: GeoJSONFeature = {
        ...existing,
        geometry: patch.newGeometry ? patch.newGeometry : existing.geometry,
        properties: patch.removeAllProperties ? {} : { ...(existing.properties ?? {}) },
      };
      if (!patch.removeAllProperties && Array.isArray(patch.removeProperties)) {
        for (const key of patch.removeProperties) delete next.properties[key];
      }
      if (Array.isArray(patch.addOrUpdateProperties)) {
        for (const item of patch.addOrUpdateProperties) next.properties[item.key] = item.value;
      }
      byId.set(id, next);
      update.push(patch);
    }
    if (add.length > 0) runtimeDiff.add = add;
    if (update.length > 0) runtimeDiff.update = update;
  }

  return {
    featureCollection: {
      type: 'FeatureCollection',
      features: [...byId.values()],
    },
    runtimeDiff,
  };
}

function cloneFeatureForDiff(raw: unknown): GeoJSONFeature {
  const feature = raw as GeoJSONFeature;
  return {
    ...feature,
    geometry: feature.geometry,
    properties: { ...(feature.properties ?? {}) },
  };
}

function normalizeFeatureCollectionIdentities(fc: GeoJSONFeatureCollection): GeoJSONFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: fc.features.map(normalizeFeatureIdentity),
  };
}

function normalizeFeatureIdentity(raw: unknown): GeoJSONFeature {
  const feature = cloneFeatureForDiff(raw);
  const fallbackId = feature.properties?.id;
  if (feature.id == null && isFeatureId(fallbackId)) {
    return { ...feature, id: fallbackId };
  }
  return feature;
}

function featureToReplacementPatch(feature: GeoJSONFeature): NonNullable<GeoJSONSourceDiff['update']>[number] {
  const id = featureIdentityValue(feature);
  return {
    id: id ?? feature.id!,
    newGeometry: feature.geometry,
    removeAllProperties: true,
    addOrUpdateProperties: Object.entries(feature.properties ?? {}).map(([key, value]) => ({ key, value })),
  };
}

function normalizeFeaturePatch(raw: unknown): NonNullable<GeoJSONSourceDiff['update']>[number] | null {
  if (!raw || typeof raw !== 'object') return null;
  const patch = raw as Record<string, unknown>;
  if (!isFeatureId(patch.id)) return null;
  return {
    id: patch.id,
    ...(isGeoJSONGeometry(patch.newGeometry) ? { newGeometry: patch.newGeometry } : {}),
    ...(typeof patch.removeAllProperties === 'boolean' ? { removeAllProperties: patch.removeAllProperties } : {}),
    ...(Array.isArray(patch.removeProperties)
      ? { removeProperties: patch.removeProperties.filter((key): key is string => typeof key === 'string') }
      : {}),
    ...(Array.isArray(patch.addOrUpdateProperties)
      ? {
          addOrUpdateProperties: patch.addOrUpdateProperties
            .filter((item): item is { key: string; value: unknown } => (
              Boolean(item)
              && typeof item === 'object'
              && typeof (item as { key?: unknown }).key === 'string'
            ))
            .map((item) => ({ key: item.key, value: item.value })),
        }
      : {}),
  };
}

function isGeoJSONFeature(value: unknown): value is GeoJSONFeature {
  return Boolean(
    value
      && typeof value === 'object'
      && (value as { type?: unknown }).type === 'Feature'
      && (value as { geometry?: unknown }).geometry
      && typeof (value as { geometry?: { type?: unknown } }).geometry?.type === 'string',
  );
}

function isGeoJSONGeometry(value: unknown): value is GeoJSONFeature['geometry'] {
  return Boolean(
    value
      && typeof value === 'object'
      && typeof (value as { type?: unknown }).type === 'string',
  );
}

function isFeatureId(value: unknown): value is string | number {
  return typeof value === 'string' || typeof value === 'number';
}

function featureIdKey(id: unknown): string | null {
  if (typeof id === 'string' || typeof id === 'number') return `${typeof id}:${String(id)}`;
  return null;
}

function featureIdentityValue(feature: GeoJSONFeature): string | number | null {
  if (isFeatureId(feature.id)) return feature.id;
  const propertyId = feature.properties?.id;
  return isFeatureId(propertyId) ? propertyId : null;
}

function featureIdentityKey(feature: GeoJSONFeature): string | null {
  return featureIdKey(featureIdentityValue(feature));
}

function isUpdateableFeatureCollection(fc: GeoJSONFeatureCollection): boolean {
  const ids = new Set<string>();
  for (const feature of fc.features) {
    const id = featureIdentityKey(feature);
    if (id === null || ids.has(id)) return false;
    ids.add(id);
  }
  return true;
}

function hasTopLevelUpdateIds(fc: GeoJSONFeatureCollection): boolean {
  const ids = new Set<string>();
  for (const feature of fc.features) {
    const id = featureIdKey(feature.id);
    if (id === null || ids.has(id)) return false;
    ids.add(id);
  }
  return true;
}
