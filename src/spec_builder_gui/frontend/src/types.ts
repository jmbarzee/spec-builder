export type JobStatus =
  | "queued"
  | "running"
  | "waiting_for_human"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkflowPhase =
  | "acquire"
  | "extract"
  | "classify"
  | "critic"
  | "draft"
  | "best_practices_review"
  | "supervisor"
  | "waiting_for_human"
  | "waiting_for_approval"
  | "completed"
  | "cancelled"
  | "failed";

export type PendingHumanAction = "clarification" | "approval" | null;

export type UploadedArtifact = {
  artifactId: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  uploadedAt: string;
  kind: "pdf" | "image" | "markdown" | "notes";
  fileUri: string;
  workflowArtifactType: string;
  title?: string | null;
};

export type ReviewFeedbackEntry = {
  id: string;
  createdAt: string;
  author: string;
  kind: "feedback" | "approval" | "rejection" | "force_finalize" | "cancel";
  text?: string | null;
};

export type StageState = {
  stage:
    | "acquire"
    | "extract"
    | "classify"
    | "critic"
    | "draft"
    | "best_practices_review"
    | "supervisor";
  status: "not_started" | "running" | "completed" | "failed" | "skipped";
  startedAt?: string | null;
  completedAt?: string | null;
  message?: string | null;
};

export type DraftMetadata = {
  draftVersion: number;
  summary?: string | null;
  markdownSpecUri?: string | null;
  structuredSpecUri?: string | null;
  openQuestionsCount: number;
  assumptionCount: number;
};

export type BestPracticesViolation = {
  severity: "high" | "medium" | "low";
  issue: string;
  relatedPassageId?: string | null;
  suggestedFix: string;
};

export type TraceabilityEntry = {
  claim: string;
  classification: "explicit" | "inferred" | "proposed_extension";
  sourceArtifactIds: string[];
};

export type TraceabilitySummary = {
  entries: TraceabilityEntry[];
  uncoveredClaims: string[];
};

export type JobSummary = {
  jobId: string;
  workflowId: string;
  runId?: string | null;
  projectName: string;
  status: JobStatus;
  currentPhase: WorkflowPhase;
  iteration: number;
  architectureMode: boolean;
  createdAt: string;
  updatedAt: string;
};

export type JobDetail = {
  jobId: string;
  workflowId: string;
  runId?: string | null;
  projectName: string;
  status: JobStatus;
  currentPhase: WorkflowPhase;
  iteration: number;
  architectureMode: boolean;
  createdAt: string;
  updatedAt: string;
  outputDirectory: string;
  artifacts: UploadedArtifact[];
  stages: StageState[];
  openQuestions: string[];
  warnings: string[];
  currentDraftMarkdown?: string | null;
  finalSpecMarkdown?: string | null;
  reviewFeedbackHistory: ReviewFeedbackEntry[];
  bestPracticesViolations: BestPracticesViolation[];
  currentDraftMetadata?: DraftMetadata | null;
  traceabilitySummary?: TraceabilitySummary | null;
  pendingHumanAction: PendingHumanAction;
  finalWorkflowStatus?: "completed" | "needs_clarification" | "failed" | "cancelled" | null;
  artifactUris: Record<string, string>;
  canApprove: boolean;
  canReject: boolean;
  canProvideFeedback: boolean;
  canForceFinalize: boolean;
  canDownload: boolean;
  canCreateRevision: boolean;
  canApproveRevisionAsFinal: boolean;
  parentJobId?: string | null;
  revisionFeedback?: string | null;
  createdFromVersion?: number | null;
  lastSyncedAt?: string | null;
  lastError?: string | null;
};

export type CreateJobRequest = {
  projectName: string;
  outputDirectory: string;
  artifactIds: string[];
  userIntent?: string;
  requireHumanApproval?: boolean;
  architectureMode?: boolean;
  outputTargets?: Array<"markdown_spec" | "structured_json">;
};

export type CreateJobResponse = {
  jobId: string;
  workflowId: string;
  status: JobStatus;
  createdAt: string;
};

export type UploadArtifactResponse = {
  artifactId: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  kind: "pdf" | "image";
};

export type ListJobsResponse = {
  items: JobSummary[];
  total: number;
};

export type SubmitReviewRequest = {
  action: "approve" | "reject" | "feedback" | "force_finalize" | "cancel";
  text?: string;
};

export type CreateRevisionRequest = {
  projectName?: string;
  feedback: string;
};

export type CreateRevisionResponse = {
  jobId: string;
  workflowId: string;
  status: JobStatus;
};

export type PickOutputDirectoryResponse = {
  supported: boolean;
  cancelled: boolean;
  path?: string | null;
};

export type SystemFeatures = {
  architectureMode: boolean;
};

export type TwfDesign = {
  source: string;
  parse: Record<string, unknown>;
  graph: Record<string, unknown> | null;
};
