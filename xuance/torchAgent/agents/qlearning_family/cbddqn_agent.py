from xuance.torchAgent.agents import *
from xuance.cluster_tool import ClusterTool
from xuance.torchAgent.learners import *
from xuance.torchAgent.learners.qlearning_family.cbddqn_learner import *

class CBDDQN_Agent(Agent):
    def __init__(self,
                 config: Namespace,
                 envs: DummyVecEnv_Gym,
                 policy: nn.Module,
                 optimizer: torch.optim.Optimizer,
                 scheduler: Optional[torch.optim.lr_scheduler._LRScheduler] = None,
                 device: Optional[Union[int, str, torch.device]] = None):
        self.render = config.render
        self.n_envs = envs.num_envs

        self.gamma = config.gamma
        self.train_frequency = config.training_frequency
        self.start_training = config.start_training
        self.start_greedy = config.start_greedy
        self.end_greedy = config.end_greedy
        self.egreedy = config.start_greedy
        self.beta_t = 0.0
        self.beta_max = config.beta_max
        self.beta_step = 0
        self.k = config.k

        self.observation_space = envs.observation_space
        self.action_space = envs.action_space
        self.auxiliary_info_shape = {}
        self.atari = True if config.env_name == "Atari" else False
        Buffer = DummyOffPolicyBuffer_Atari if self.atari else DummyOffPolicyBuffer
        memory = Buffer(self.observation_space,
                        self.action_space,
                        self.auxiliary_info_shape,
                        self.n_envs,
                        config.buffer_size,
                        config.batch_size)
        learner = CBDDQN_Learner(policy,
                                 optimizer,
                                 scheduler,
                                 config.device,
                                 config.model_dir,
                                 config.gamma,
                                 config.sync_frequency)

        self.cluster_tool = ClusterTool(np.random.rand(1000, space2shape(self.observation_space)[0]),
                                        self.action_space.n,
                                        config.n_clusters)

        super(CBDDQN_Agent, self).__init__(config, envs, policy, memory, learner, device, config.log_dir, config.model_dir)

    def _action(self, obs, egreedy=0.0):
        _, argmax_action, _ = self.policy(obs)
        random_action = np.random.choice(self.action_space.n, self.n_envs)
        if np.random.rand() < egreedy:
            action = random_action
        else:
            action = argmax_action.detach().cpu().numpy()
        return action

    def train(self, train_steps):
        obs = self.envs.buf_obs
        for _ in tqdm(range(train_steps)):
            step_info = {}
            self.obs_rms.update(obs)
            obs = self._process_observation(obs)
            acts = self._action(obs, self.egreedy)
            next_obs, rewards, terminals, trunctions, infos = self.envs.step(acts)

            # 更新动作选择频率
            for o, a in zip(obs, acts):
                self.cluster_tool.update_action_counts(o, a)

            self.memory.store(obs, acts, self._process_reward(rewards), terminals, self._process_observation(next_obs))
            if self.current_step > self.start_training and self.current_step % self.train_frequency == 0:
                # training
                obs_batch, act_batch, rew_batch, terminal_batch, next_batch = self.memory.sample()

                # 计算b_t(a|s_{t+1})
                belief_distributions = []
                for state in next_batch:
                    immediate_belief = self.policy(state)[2].detach().cpu().numpy()
                    beta_t = min(self.beta_t + self.beta_step, self.beta_max)
                    self.beta_t = beta_t  # 更新beta_t
                    belief_distribution = self.cluster_tool.compute_belief_distribution(state, beta_t, immediate_belief, self.k)
                    belief_distributions.append(belief_distribution)

                # 更新Q值
                step_info = self.learner.update(obs_batch, act_batch, rew_batch, next_batch, terminal_batch, belief_distributions)
                step_info["epsilon-greedy"] = self.egreedy
                self.log_infos(step_info, self.current_step)

            obs = next_obs
            for i in range(self.n_envs):
                if terminals[i] or trunctions[i]:
                    if self.atari and (~trunctions[i]):
                        pass
                    else:
                        obs[i] = infos[i]["reset_obs"]
                        self.current_episode[i] += 1
                        self.beta_step += 1
                        if self.use_wandb:
                            step_info["Episode-Steps/env-%d" % i] = infos[i]["episode_step"]
                            step_info["Train-Episode-Rewards/env-%d" % i] = infos[i]["episode_score"]
                        else:
                            step_info["Episode-Steps"] = {"env-%d" % i: infos[i]["episode_step"]}
                            step_info["Train-Episode-Rewards"] = {"env-%d" % i: infos[i]["episode_score"]}
                        self.log_infos(step_info, self.current_step)

            self.current_step += self.n_envs
            if self.egreedy >= self.end_greedy:
                self.egreedy = self.egreedy - (self.start_greedy - self.end_greedy) / self.config.decay_step_greedy

    def test(self, env_fn, test_episodes):
        test_envs = env_fn()
        num_envs = test_envs.num_envs
        videos, episode_videos = [[] for _ in range(num_envs)], []
        current_episode, scores, best_score = 0, [], -np.inf
        obs, infos = test_envs.reset()
        if self.config.render_mode == "rgb_array" and self.render:
            images = test_envs.render(self.config.render_mode)
            for idx, img in enumerate(images):
                videos[idx].append(img)

        while current_episode < test_episodes:
            self.obs_rms.update(obs)
            obs = self._process_observation(obs)
            acts = self._action(obs, egreedy=0.0)
            next_obs, rewards, terminals, trunctions, infos = test_envs.step(acts)
            if self.config.render_mode == "rgb_array" and self.render:
                images = test_envs.render(self.config.render_mode)
                for idx, img in enumerate(images):
                    videos[idx].append(img)

            obs = next_obs
            for i in range(num_envs):
                if terminals[i] or trunctions[i]:
                    if self.atari and (~trunctions[i]):
                        pass
                    else:
                        obs[i] = infos[i]["reset_obs"]
                        scores.append(infos[i]["episode_score"])
                        current_episode += 1
                        if best_score < infos[i]["episode_score"]:
                            best_score = infos[i]["episode_score"]
                            episode_videos = videos[i].copy()
                        if self.config.test_mode:
                            print("Episode: %d, Score: %.2f" % (current_episode, infos[i]["episode_score"]))

        if self.config.render_mode == "rgb_array" and self.render:
            # time, height, width, channel -> time, channel, height, width
            videos_info = {"Videos_Test": np.array([episode_videos], dtype=np.uint8).transpose((0, 1, 4, 2, 3))}
            self.log_videos(info=videos_info, fps=self.fps, x_index=self.current_step)

        if self.config.test_mode:
            print("Best Score: %.2f" % (best_score))

        test_info = {
            "Test-Episode-Rewards/Mean-Score": np.mean(scores),
            "Test-Episode-Rewards/Std-Score": np.std(scores)
        }
        self.log_infos(test_info, self.current_step)

        test_envs.close()

        return scores