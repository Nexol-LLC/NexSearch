//! Bounded asynchronous jobs. Dropping a task releases both slot and account lock.
use std::{
    collections::{HashMap, HashSet},
    future::Future,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::{Mutex as AsyncMutex, Semaphore, watch};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JobError {
    AlreadyRunning,
    TimedOut,
    Cancelled,
    Failed(String),
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Phase {
    Running,
    Succeeded,
    TimedOut,
    Cancelled,
    Failed,
}
#[derive(Debug, Clone)]
pub struct JobStatus {
    pub phase: Phase,
    pub started_at: Instant,
    pub finished_at: Option<Instant>,
}

#[derive(Clone)]
pub struct Cancellation(watch::Sender<bool>);
impl Default for Cancellation {
    fn default() -> Self {
        Self(watch::channel(false).0)
    }
}
impl Cancellation {
    pub fn cancel(&self) {
        self.0.send_replace(true);
    }
    pub async fn cancelled(&self) {
        let mut rx = self.0.subscribe();
        loop {
            if *rx.borrow_and_update() {
                return;
            }
            if rx.changed().await.is_err() {
                return;
            }
        }
    }
}

#[derive(Clone)]
pub struct Coordinator {
    accounts: Arc<Mutex<HashMap<Uuid, Arc<AsyncMutex<()>>>>>,
    slots: Arc<Semaphore>,
    status: Arc<Mutex<HashMap<Uuid, JobStatus>>>,
}
impl Coordinator {
    pub fn new(parallelism: usize) -> Result<Self, &'static str> {
        if !(1..=64).contains(&parallelism) {
            return Err("Число одновременных задач: от 1 до 64");
        }
        Ok(Self {
            accounts: Arc::default(),
            slots: Arc::new(Semaphore::new(parallelism)),
            status: Arc::default(),
        })
    }
    pub fn status(&self, account: Uuid) -> Option<JobStatus> {
        self.status.lock().unwrap().get(&account).cloned()
    }
    pub async fn run<T, F>(
        &self,
        account: Uuid,
        deadline: Duration,
        cancel: Cancellation,
        operation: F,
    ) -> Result<T, JobError>
    where
        F: Future<Output = Result<T, JobError>>,
    {
        let lock = self
            .accounts
            .lock()
            .unwrap()
            .entry(account)
            .or_default()
            .clone();
        let _account = lock
            .try_lock_owned()
            .map_err(|_| JobError::AlreadyRunning)?;
        self.status.lock().unwrap().insert(
            account,
            JobStatus {
                phase: Phase::Running,
                started_at: Instant::now(),
                finished_at: None,
            },
        );
        let mut completion = Completion {
            status: self.status.clone(),
            account,
            phase: Phase::Cancelled,
        };
        let work = async {
            let _slot = self
                .slots
                .acquire()
                .await
                .map_err(|_| JobError::Cancelled)?;
            operation.await
        };
        let result = tokio::select! { biased;
            _=cancel.cancelled()=>Err(JobError::Cancelled),
            result=tokio::time::timeout(deadline,work)=>result.unwrap_or(Err(JobError::TimedOut))
        };
        completion.phase = match &result {
            Ok(_) => Phase::Succeeded,
            Err(JobError::TimedOut) => Phase::TimedOut,
            Err(JobError::Cancelled) => Phase::Cancelled,
            _ => Phase::Failed,
        };
        result
    }
}
struct Completion {
    status: Arc<Mutex<HashMap<Uuid, JobStatus>>>,
    account: Uuid,
    phase: Phase,
}
impl Drop for Completion {
    fn drop(&mut self) {
        if let Some(status) = self.status.lock().unwrap().get_mut(&self.account) {
            status.phase = self.phase.clone();
            status.finished_at = Some(Instant::now());
        }
    }
}

/// In-memory reference policy for tests/preview. Production claims require a PostgreSQL unique constraint.
#[derive(Default)]
pub struct ClaimRegistry(Mutex<HashSet<String>>);
impl ClaimRegistry {
    pub fn claim(&self, inn: &str) -> bool {
        self.0.lock().unwrap().insert(inn.to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test(start_paused = true)]
    async fn timeout_clears_running_and_releases_slot() {
        let c = Coordinator::new(1).unwrap();
        let id = Uuid::new_v4();
        assert_eq!(
            c.run::<(), _>(
                id,
                Duration::from_secs(2),
                Cancellation::default(),
                std::future::pending()
            )
            .await,
            Err(JobError::TimedOut)
        );
        assert_eq!(c.status(id).unwrap().phase, Phase::TimedOut);
        assert_eq!(
            c.run(id, Duration::from_secs(2), Cancellation::default(), async {
                Ok(3)
            })
            .await,
            Ok(3)
        );
    }
    #[tokio::test]
    async fn duplicate_requests_coalesce_and_abort_does_not_leave_syncing_forever() {
        let c = Coordinator::new(1).unwrap();
        let id = Uuid::new_v4();
        let copy = c.clone();
        let task = tokio::spawn(async move {
            copy.run::<(), _>(
                id,
                Duration::from_secs(60),
                Cancellation::default(),
                std::future::pending(),
            )
            .await
        });
        while c.status(id).is_none() {
            tokio::task::yield_now().await;
        }
        assert_eq!(
            c.run(id, Duration::from_secs(1), Cancellation::default(), async {
                Ok(())
            })
            .await,
            Err(JobError::AlreadyRunning)
        );
        task.abort();
        let _ = task.await;
        assert_eq!(c.status(id).unwrap().phase, Phase::Cancelled);
        assert!(
            c.run(id, Duration::from_secs(1), Cancellation::default(), async {
                Ok(())
            })
            .await
            .is_ok()
        );
    }
    #[tokio::test]
    async fn cancel_while_waiting_for_global_slot_is_immediate() {
        let c = Coordinator::new(1).unwrap();
        let copy = c.clone();
        let id = Uuid::new_v4();
        let first = tokio::spawn(async move {
            copy.run::<(), _>(
                id,
                Duration::from_secs(60),
                Cancellation::default(),
                std::future::pending(),
            )
            .await
        });
        while c.status(id).is_none() {
            tokio::task::yield_now().await;
        }
        let cancel = Cancellation::default();
        cancel.cancel();
        assert_eq!(
            c.run(Uuid::new_v4(), Duration::from_secs(60), cancel, async {
                Ok(())
            })
            .await,
            Err(JobError::Cancelled)
        );
        first.abort();
    }
    #[test]
    fn simultaneous_claims_have_only_one_winner() {
        let registry = Arc::new(ClaimRegistry::default());
        let jobs: Vec<_> = (0..8)
            .map(|_| {
                let r = registry.clone();
                std::thread::spawn(move || r.claim("7707083893"))
            })
            .collect();
        assert_eq!(
            jobs.into_iter()
                .filter_map(|j| j.join().ok())
                .filter(|v| *v)
                .count(),
            1
        );
    }
    #[tokio::test]
    async fn independent_accounts_use_different_slots() {
        let c = Coordinator::new(4).unwrap();
        let barrier = Arc::new(tokio::sync::Barrier::new(4));
        let mut jobs = vec![];
        for _ in 0..4 {
            let c = c.clone();
            let b = barrier.clone();
            jobs.push(tokio::spawn(async move {
                c.run(
                    Uuid::new_v4(),
                    Duration::from_secs(3),
                    Cancellation::default(),
                    async {
                        b.wait().await;
                        Ok(())
                    },
                )
                .await
            }));
        }
        for task in jobs {
            assert_eq!(task.await.unwrap(), Ok(()));
        }
    }
}
