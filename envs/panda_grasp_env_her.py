import os
import time
from collections import OrderedDict

import gym
import numpy as np
import pybullet as p
import pybullet_data
from gym import spaces
import robot_data
from . import PandaEnv


class PandaGraspGymEnv(gym.GoalEnv):
    metadata = {'render.modes': ['human', 'rgb_array'],
                'video.frames_per_second': 50}

    def __init__(self,
                 urdf_root=robot_data.getDataPath(),
                 use_ik=True,
                 is_discrete=0,
                 action_repeat_amount=1,
                 is_rendering=False,
                 max_step_count=1000,
                 is_target_position_fixed=False,
                 num_controlled_joints=7,
                 is_continuous_downward_enabled=False):
        self.target_object_height = 0.7
        self.episode_number = 1
        self.grasp_attempts_count = 0
        self.largest_observation_value = 1
        self.largest_action_value = 1
        self.img_height = 720
        self.img_width = 960
        self.distance_to_grasp = 0.11
        self._is_continuous_downward_enabled = is_continuous_downward_enabled
        self._attempted_grasp = False
        self._num_controlled_joints = num_controlled_joints
        self._is_discrete = is_discrete
        self._time_step = 1. / 240.
        self._use_ik = use_ik
        self._urdf_root = urdf_root
        self._action_repeat_amount = action_repeat_amount
        self._observation = []
        self._env_step_counter = 0
        self._is_rendering = is_rendering
        self._max_step_count = max_step_count
        self._terminated = False
        self._cam_dist = 1.3
        self._cam_yaw = 180
        self._cam_pitch = -40
        self._table_height = 0
        self._p = p
        self._is_target_position_fixed = is_target_position_fixed
        self.successful_grasp_count = 0
        self._panda = None
        self.target_object_id = None
        self.successful_grasp = False

        if self._is_discrete:
            self.action_space = 6
        else:
            self.action_space = self._num_controlled_joints

        if self._is_rendering:
            cid = p.connect(p.SHARED_MEMORY)
            if cid < 0:
                p.connect(p.GUI)
            p.resetDebugVisualizerCamera(1.5, 125, -30, [0.52, -0.2, 0.33])
        else:
            p.connect(p.DIRECT)
        self._observation = self.reset()
        observation_dim = len(self._observation['observation'])
        observation_high = np.array([self.largest_observation_value] * observation_dim)
        self.observation_space = spaces.Box(-observation_high, observation_high, dtype='float32')

        if self._is_discrete:
            self.action_space = spaces.Discrete(self.action_space)

        else:
            action_high = np.array([self.largest_action_value] * self.action_space)
            self.action_space = spaces.Box(-action_high, action_high, dtype='float32')

    def reset(self):
        self.successful_grasp = False
        self._terminated = False
        self._attempted_grasp = False
        self._env_step_counter = 0
        p.resetSimulation()
        p.setPhysicsEngineParameter(numSolverIterations=150)
        p.setTimeStep(self._time_step)

        p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"), useFixedBase=True)
        # Load robot
        self._panda = PandaEnv([0, 0, 0.625], self._urdf_root, time_step=self._time_step,
                               use_ik=self._use_ik, num_controlled_joints=self._num_controlled_joints)

        # Load table and object for simulation
        table_id = p.loadURDF(os.path.join(self._urdf_root, "franka/table.urdf"), useFixedBase=True)

        table_info = p.getVisualShapeData(table_id, -1)[0]

        self._table_height = table_info[5][2] + table_info[3][2] + 0.01

        # limit panda workspace to table plane
        self._panda.workspace_lim[2][0] = self._table_height
        # Randomize start position of object and target.

        if self._is_target_position_fixed:
            target_orn = p.getQuaternionFromEuler([0, 0, np.random.uniform(-np.pi, np.pi)])
            self.target_object_id = p.loadURDF(os.path.join(self._urdf_root, "franka/cube_small.urdf"),
                                               basePosition=[0.7, 0.25, self._table_height], baseOrientation=target_orn,
                                               useFixedBase=False,
                                               globalScaling=.5)
        else:
            self.target_object_id = p.loadURDF(os.path.join(self._urdf_root, "franka/cube_small.urdf"),
                                               basePosition=self._sample_pose()[0], useFixedBase=False,
                                               globalScaling=.5)

        p.setGravity(0, 0, -9.8)

        p.stepSimulation()

        self._observation = self.get_extended_observation()

        return self._observation

    def get_extended_observation(self):
        # get robot observations
        observation = self._panda.get_observation()
        target_obj_pos, target_obj_orn = p.getBasePositionAndOrientation(self.target_object_id)
        achieved_goal = target_obj_pos[2]
        observation.extend(list(target_obj_pos))
        observation.extend(list(target_obj_orn))
        observation.append(self.target_object_height)

        return OrderedDict([
            ('observation', np.asarray(observation.copy())),
            ('achieved_goal', np.asarray(achieved_goal)),
            ('desired_goal', np.asarray(self.target_object_height))
        ])

    def step(self, action):
        if self._is_discrete:
            delta_pos = 0.01
            delta_angle = 0.01
            dx = [ -delta_pos, delta_pos, 0, 0, 0, 0, 0, 0][action]
            dy = [0, 0, -delta_pos, delta_pos, 0, 0, 0, 0][action]
            if self._is_continuous_downward_enabled:
                dz = -delta_pos
            else:
                dz = [0, 0, 0, 0, -delta_pos, delta_pos, 0, 0][action]

            # droll = [0, 0, 0, 0, 0, 0, 0, -delta_angle, delta_angle, 0, 0, 0, 0][action]
            # dpitch = [0, 0, 0, 0, 0, 0, 0, 0, 0, -delta_angle, delta_angle, 0, 0][action]
            dyaw = [0, 0, 0, 0, 0, 0, 0, -delta_angle, delta_angle][action]
            f = 1
            real_action = [dx, dy, dz, 0, 0, dyaw, f]
            return self.real_step(real_action)
        else:
            delta_pos = 1.5
            dx = action[0] * delta_pos
            dy = action[1] * delta_pos
            dz = action[2] * delta_pos
            droll = action[3] * delta_pos
            dpitch = action[4] * delta_pos
            dyaw = action[5] * delta_pos
            f = 1
            real_action = [dx, dy, dz, droll, dpitch, dyaw, f]
            return self.real_step(real_action)

    def real_step(self, action):
        for i in range(self._action_repeat_amount):
            self._panda.apply_action(action)
            p.stepSimulation()
            if self._termination():
                break
            self._env_step_counter += 1

        if self._is_rendering:
            time.sleep(self._time_step)

        self._observation = self.get_extended_observation()

        distance = self.get_distance_to_target()

        if distance <= self.distance_to_grasp:
            self.grasp_attempts_count += 1
            self.perform_grasp()
            self._observation = self.get_extended_observation()

        reward = self._compute_reward()
        done = self._termination()

        if done:
            self.episode_number += 1

        return np.array(self._observation), reward, done, {"is_success": self.successful_grasp}

    def _termination(self):
        return self._attempted_grasp or self._env_step_counter >= self._max_step_count

    def perform_grasp(self):
        anim_length = 100
        self._panda.update_gripper_pos()
        self.close_fingers(anim_length)
        self.lift_gripper(anim_length)
        self._attempted_grasp = True

    def close_fingers(self, anim_length):
        finger_angle = 1
        for i in range(anim_length):
            grasp_action = [0, 0, 0, 0, 0, 0, finger_angle]
            self._panda.apply_action(grasp_action)
            p.stepSimulation()
            finger_angle -= 1 / anim_length;
            if finger_angle < 0:
                finger_angle = 0

    def lift_gripper(self, anim_length):
        for i in range(anim_length):
            grasp_action = [0, 0, 0.005, 0, 0, 0, 0]
            self._panda.apply_action(grasp_action)
            p.stepSimulation()
            block_pos, block_orn = p.getBasePositionAndOrientation(self.target_object_id)
            if block_pos[2] > 0.8:
                break
            state = p.getLinkState(self._panda.panda_id, self._panda.gripper_index)
            actual_end_effector_pos = state[0]
            if actual_end_effector_pos[2] > 2:
                break

    def get_distance(self, point_a, point_b, axis):
        point_a = np.array([axis[0] * point_a[0], axis[1] * point_a[1], axis[2] * point_a[2]])
        point_b = np.array([axis[0] * point_b[0], axis[1] * point_b[1], axis[2] * point_b[2]])
        return np.linalg.norm(point_a - point_b)

    def get_horizontal_distance_to_target(self):
        target_obj_pos, _ = self.get_target_pos()
        gripper_pos = self.get_gripper_pos()
        return self.get_distance(gripper_pos, target_obj_pos, [1, 1, 0])

    def get_distance_to_target(self):
        target_obj_pos = self.get_target_pos()
        gripper_pos = self.get_gripper_pos()
        return self.get_distance(gripper_pos, target_obj_pos, [1, 1, 1])

    def _compute_reward(self):
        target_obj_pos = self.get_target_pos()
        gripper_pos = self.get_gripper_pos()

        if target_obj_pos[2] > self.target_object_height:
            reward = 1000000
            self.successful_grasp = True
            self.successful_grasp_count += 1
            print("successfully grasped a block!!!")
        else:
            horizontal_distance = self.get_distance(gripper_pos, target_obj_pos, [1, 1, 0])
            reward = - horizontal_distance * 100
            if horizontal_distance <= .03:
                reward = - self.get_distance_to_target() * 100
            else:
                reward -= 500

        return reward

    def _sample_pose(self):
        ws_lim = self._panda.workspace_lim
        x = np.random.uniform(low=ws_lim[0][0], high=ws_lim[0][1], size=1)
        y = np.random.uniform(low=ws_lim[1][0], high=ws_lim[1][1], size=1)
        z = self._table_height
        obj_pose = [x, y, z]

        return obj_pose

    def get_gripper_pos(self):
        return self._observation[0:3]

    def get_target_pos(self):
        return self._observation[7:10]

    def render(self, mode="rgb_array", close=False):
        if mode != "rgb_array":
            return np.array([])

        base_pos, orn = self._p.getBasePositionAndOrientation(self._panda.panda_id)
        view_matrix = self._p.computeViewMatrixFromYawPitchRoll(cameraTargetPosition=base_pos,
                                                                distance=self._cam_dist,
                                                                yaw=self._cam_yaw,
                                                                pitch=self._cam_pitch,
                                                                roll=0,
                                                                upAxisIndex=2)
        proj_matrix = self._p.computeProjectionMatrixFOV(fov=60,
                                                         aspect=float(self.img_width) / self.img_height,
                                                         nearVal=0.1,
                                                         farVal=100.0)
        (_, _, px, _, _) = self._p.getCameraImage(width=self.img_width,
                                                  height=self.img_height,
                                                  viewMatrix=view_matrix,
                                                  projectionMatrix=proj_matrix,
                                                  renderer=self._p.ER_BULLET_HARDWARE_OPENGL)
        # renderer=self._p.ER_TINY_RENDERER)

        rgb_array = np.array(px, dtype=np.uint8)
        rgb_array = np.reshape(rgb_array, (self.img_height, self.img_width, 4))

        rgb_array = rgb_array[:, :, :3]
        return rgb_array
