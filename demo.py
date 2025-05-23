import argparse
from xuance import get_runner


def parse_args():
    parser = argparse.ArgumentParser("Run a demo.")
    parser.add_argument("--method", type=str, default="cbdsac")
    parser.add_argument("--env", type=str, default="mujoco")
    parser.add_argument("--env-id", type=str, default="Ant-v4")
    parser.add_argument("--test", type=int, default=0)
    parser.add_argument("--device", type=str, default="cuda:0")

    return parser.parse_args()

if __name__ == '__main__':
    parser = parse_args()
    ########################################################################
    # You can also modify the arguments not listed above here. For example:
    # parser.parallels = 1
    # parser.running_steps = 1000000
    # ...
    ########################################################################
    runner = get_runner(method=parser.method,
                        env=parser.env,
                        env_id=parser.env_id,
                        parser_args=parser,
                        is_test=parser.test)
    runner.run()