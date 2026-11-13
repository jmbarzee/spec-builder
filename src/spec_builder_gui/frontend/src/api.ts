import type {
  CreateJobRequest,
  CreateJobResponse,
  CreateRevisionRequest,
  CreateRevisionResponse,
  DecideToolCallsRequest,
  JobDetail,
  ListJobsResponse,
  PickOutputDirectoryResponse,
  SubmitReviewRequest,
  SystemFeatures,
  TwfDesign,
  UploadArtifactResponse,
} from "./types";

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: string };
      if (payload.error) {
        message = payload.error;
      }
    } catch {
      // Ignore JSON parse failures for non-JSON error bodies.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const api = {
  uploadArtifact(file: File): Promise<UploadArtifactResponse> {
    const formData = new FormData();
    formData.append("file", file);
    return requestJson<UploadArtifactResponse>("/api/artifacts", {
      method: "POST",
      body: formData,
    });
  },
  createJob(payload: CreateJobRequest): Promise<CreateJobResponse> {
    return requestJson<CreateJobResponse>("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
  listJobs(params: {
    status?: string;
    search?: string;
    page?: number;
    pageSize?: number;
  }): Promise<ListJobsResponse> {
    const searchParams = new URLSearchParams();
    if (params.status) {
      searchParams.set("status", params.status);
    }
    if (params.search) {
      searchParams.set("search", params.search);
    }
    searchParams.set("page", String(params.page ?? 1));
    searchParams.set("pageSize", String(params.pageSize ?? 20));
    return requestJson<ListJobsResponse>(`/api/jobs?${searchParams.toString()}`);
  },
  getJobDetail(jobId: string): Promise<JobDetail> {
    return requestJson<JobDetail>(`/api/jobs/${jobId}`);
  },
  getTwfDesign(jobId: string): Promise<TwfDesign> {
    return requestJson<TwfDesign>(`/api/jobs/${jobId}/twf`);
  },
  submitReview(jobId: string, payload: SubmitReviewRequest): Promise<JobDetail> {
    return requestJson<JobDetail>(`/api/jobs/${jobId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
  decideToolCalls(jobId: string, payload: DecideToolCallsRequest): Promise<JobDetail> {
    return requestJson<JobDetail>(`/api/jobs/${jobId}/tool-decisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
  createRevision(jobId: string, payload: CreateRevisionRequest): Promise<CreateRevisionResponse> {
    return requestJson<CreateRevisionResponse>(`/api/jobs/${jobId}/revise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
  approveRevisionAsFinal(jobId: string): Promise<JobDetail> {
    return requestJson<JobDetail>(`/api/jobs/${jobId}/approve-revision`, {
      method: "POST",
    });
  },
  getFeatures(): Promise<SystemFeatures> {
    return requestJson<SystemFeatures>("/api/system/features");
  },
  pickOutputDirectory(): Promise<PickOutputDirectoryResponse> {
    return requestJson<PickOutputDirectoryResponse>("/api/system/pick-output-directory", {
      method: "POST",
    });
  },
  downloadUrl(jobId: string): string {
    return `/api/jobs/${jobId}/download`;
  },
};
