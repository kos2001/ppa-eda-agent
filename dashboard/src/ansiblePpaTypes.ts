export interface MatrixPackage {
  name: string;
  version_specifier_set: string;
  dists: string[];
}

export interface MatrixEntry {
  entryName: string;
  githubBranch: string;
  packages: MatrixPackage[];
}

export interface ResolvedVersion {
  matrix_entry: string;
  branch: string;
  package: string;
  version_specifier_set: string;
  resolved_version: string | null;
  dists: string[];
}

export interface AnsiblePpaSnapshot {
  generatedAt: string;
  branches: string[];
  matrix: MatrixEntry[];
  resolvedVersions: ResolvedVersion[];
  recentCommits: { hash: string; message: string }[];
  buildEnvConfigured: boolean;
}
