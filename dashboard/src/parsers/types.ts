export type ParseResult<T> = { ok: true; data: T } | { ok: false; error: string };

export interface AreaResult {
  numPorts?: number;
  numNets?: number;
  numCells?: number;
  totalCombinationalArea: number;
  totalNoncombinationalArea: number;
  totalBufInvArea?: number;
  totalMacroArea: number;
  totalCellArea: number;
}

export interface TimingPath {
  startpoint: string;
  endpoint: string;
  pathGroup: string;
  slack: number;
  violated: boolean;
}

export interface TimingResult {
  paths: TimingPath[];
}

export interface PowerResult {
  cellInternalPowerMw: number;
  netSwitchingPowerMw: number;
  totalDynamicPowerMw: number;
  cellLeakagePowerMw: number;
  totalPowerMw: number;
}
